"""
retry.py — exponential-backoff retry для transient-збоїв (Фаза 3.2).

Контекст (F-ORCH-1/2): жодного retry в пайплайні не було — мережевий схлип
Ollama/OpenAlex чи рестарт Ray-актора назавжди позначав статтю failed; падіння
актора вішало ray.get() навічно. Цей модуль дає:

  retry_call(fn, ...)   — повторити виклик із backoff на retryable-винятках
  ray_get(ref, ...)     — ray.get із таймаутом (ніколи не вічний hang)

Політика: retry — ЛИШЕ для transient-класів (timeout, connection, actor
restart). Програмні помилки (TypeError, KeyError…) пропливають одразу —
ховати їх повторними спробами заборонено.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# Стабільні дефолти; override через env
RETRY_ATTEMPTS       = int(os.getenv("RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF_S      = float(os.getenv("RETRY_BACKOFF_S", "2.0"))
RAY_TASK_TIMEOUT_S   = float(os.getenv("RAY_TASK_TIMEOUT_S", "900"))   # ціла стаття
RAY_ACTOR_TIMEOUT_S  = float(os.getenv("RAY_ACTOR_TIMEOUT_S", "300"))  # один виклик актора


def _default_retryable() -> tuple[type[BaseException], ...]:
    """Transient-класи: мережа + Ray actor/worker збої (lazy import ray)."""
    classes: list[type[BaseException]] = [TimeoutError, ConnectionError, OSError]
    try:
        import requests
        classes += [requests.exceptions.Timeout, requests.exceptions.ConnectionError]
    except ImportError:
        pass
    try:
        import ray.exceptions as rex
        classes += [rex.GetTimeoutError, rex.RayActorError, rex.WorkerCrashedError]
    except ImportError:
        pass
    return tuple(classes)


def retry_call(
    fn: Callable[..., T],
    *args: Any,
    attempts: int = RETRY_ATTEMPTS,
    backoff_s: float = RETRY_BACKOFF_S,
    retryable: tuple[type[BaseException], ...] | None = None,
    label: str = "",
    **kwargs: Any,
) -> T:
    """
    Викликати fn з retry на transient-винятках (exponential backoff).

    Остання невдала спроба — виняток пропливає до викликача: retry не
    перетворює permanent-збій на мовчазний пропуск.
    """
    retryable = retryable or _default_retryable()
    label = label or getattr(fn, "__name__", "call")
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except retryable as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = backoff_s * (2 ** (attempt - 1))
            log.warning(
                "[retry] %s: спроба %d/%d впала (%s: %s) — повтор за %.1fs",
                label, attempt, attempts, type(exc).__name__,
                str(exc)[:120], delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def ray_get(ref: Any, timeout_s: float = RAY_ACTOR_TIMEOUT_S, label: str = "") -> Any:
    """
    ray.get із таймаутом — захист від вічного hang при смерті актора
    (F-ORCH-2). GetTimeoutError пропливає до викликача (стаття → FAIL,
    прогін продовжується).
    """
    import ray
    try:
        return ray.get(ref, timeout=timeout_s)
    except Exception:
        if label:
            log.error("[ray_get] %s не завершився за %.0fs або впав", label, timeout_s)
        raise
