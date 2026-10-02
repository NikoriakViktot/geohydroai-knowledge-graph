"""translate: the staged English manuscript into every other language of the manifest (numbers locked).

Reads the manuscript staged by ``assemble`` (data/workbench/<project>/out/<manuscript_out with en>),
uses the paper's glossary (``paths.glossary``), and stages manuscript_out with {lang} for each
further language. Rejected paragraphs stay in English under a [PENDING OPEN9n] marker.
"""

from __future__ import annotations

from src.workbench.assemble import translate as T
from src.workbench.steps import Context, project_dir, stage


def run(ctx: Context, call=None) -> int:
    m = ctx.manifest()
    if m is None or not m.paths.manuscript_out:
        print(f"{ctx.project_id}: the manifest needs paths.manuscript_out")
        return 1
    others = [lang for lang in m.languages if lang != "en"]
    if not others:
        print(f"{ctx.project_id}: languages {m.languages}: nothing to translate")
        return 0
    if "{lang}" not in m.paths.manuscript_out:
        print(f"{ctx.project_id}: paths.manuscript_out needs a {{lang}} part to hold more than one language")
        return 1
    src = project_dir(ctx.project_id) / "out" / m.paths.manuscript_out.replace("{lang}", "en")
    if not src.is_file():
        print(f"{ctx.project_id}: no assembled English manuscript staged; run assemble first")
        return 1
    glossary = T.load_glossary((ctx.remote.read(m.paths.glossary) or b"").decode("utf-8") if m.paths.glossary else None)
    if call is None:
        key = T.gemini_key()
        if not key:
            print("GEMINI_API_KEY / GOOGLE_API_KEY not set")
            return 1
        call = lambda prompt: T.call_gemini(prompt, key)  # noqa: E731
    status = 0
    for lang in others:
        if lang != "uk":
            print(f"  {lang}: only Ukrainian prompts exist; skipped")
            continue
        text, failed = T.translate_text(src.read_text(encoding="utf-8"), glossary, call)
        dest = m.paths.manuscript_out.replace("{lang}", lang)
        stage(ctx, dest, text)
        print(f"{ctx.project_id}: {dest} staged; {len(failed)} paragraph(s) kept in English under a marker")
        status = 2 if failed else status
    return status
