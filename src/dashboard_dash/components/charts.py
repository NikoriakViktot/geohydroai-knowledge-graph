"""
charts.py  —  Reusable Plotly figure factories
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from src.dashboard_dash.theme import COLORS, CHART_LAYOUT, CYAN_SCALE


def _base(**overrides) -> dict:
    return {**CHART_LAYOUT, **overrides}


def empty_fig(message: str = "No data") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        **_base(
            annotations=[dict(
                text=message, x=0.5, y=0.5, xref="paper", yref="paper",
                showarrow=False, font=dict(color=COLORS["text_dim"], size=13),
            )],
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            height=280,
        )
    )
    return fig


def area_chart(df: pd.DataFrame, x: str, y: str, title: str = "") -> go.Figure:
    if df.empty:
        return empty_fig()
    fig = go.Figure(go.Scatter(
        x=df[x], y=df[y],
        mode="lines",
        fill="tozeroy",
        line=dict(color=COLORS["accent_cyan"], width=2),
        fillcolor="rgba(0,212,255,0.08)",
        hovertemplate=f"{x}: %{{x}}<br>{y}: %{{y:,}}<extra></extra>",
    ))
    fig.update_layout(**_base(title=dict(text=title, font=dict(size=13)), height=260))
    return fig


def bar_h(df: pd.DataFrame, x: str, y: str, title: str = "", n: int = 15,
          color: str = COLORS["accent_cyan"]) -> go.Figure:
    if df.empty:
        return empty_fig()
    df = df.head(n).sort_values(x)
    fig = go.Figure(go.Bar(
        x=df[x], y=df[y], orientation="h",
        marker=dict(
            color=df[x],
            colorscale=CYAN_SCALE,
            showscale=False,
        ),
        text=df[x],
        textposition="outside",
        textfont=dict(size=11, color=COLORS["text_secondary"]),
        hovertemplate=f"{y}: %{{y}}<br>Count: %{{x:,}}<extra></extra>",
    ))
    fig.update_layout(**_base(
        title=dict(text=title, font=dict(size=13)),
        height=max(280, len(df) * 28 + 60),
        yaxis=dict(tickfont=dict(size=11), gridcolor="rgba(0,0,0,0)"),
        xaxis=dict(showgrid=True),
        bargap=0.3,
    ))
    return fig


def treemap(df: pd.DataFrame, names: str, values: str, title: str = "") -> go.Figure:
    if df.empty:
        return empty_fig()
    fig = go.Figure(go.Treemap(
        labels=df[names],
        values=df[values],
        parents=[""] * len(df),
        textinfo="label+value",
        textfont=dict(size=12, color=COLORS["text_primary"]),
        marker=dict(
            colorscale=CYAN_SCALE,
            showscale=False,
        ),
        hovertemplate="%{label}: %{value:,}<extra></extra>",
    ))
    fig.update_layout(**_base(title=dict(text=title, font=dict(size=13)), height=340))
    return fig


def donut(df: pd.DataFrame, names: str, values: str, title: str = "") -> go.Figure:
    if df.empty:
        return empty_fig()
    colors = CHART_LAYOUT["colorway"]
    fig = go.Figure(go.Pie(
        labels=df[names],
        values=df[values],
        hole=0.55,
        marker=dict(colors=colors[:len(df)], line=dict(color=COLORS["bg_base"], width=2)),
        textfont=dict(size=11, color=COLORS["text_primary"]),
        hovertemplate="%{label}: %{value:,} (%{percent})<extra></extra>",
    ))
    fig.update_layout(**_base(
        title=dict(text=title, font=dict(size=13)),
        height=300,
        showlegend=True,
        legend=dict(orientation="v", x=1.05, y=0.5),
    ))
    return fig


def histogram(series: pd.Series, title: str = "", log_x: bool = False) -> go.Figure:
    if series.empty:
        return empty_fig()
    data = series.dropna()
    if log_x:
        import numpy as np
        data = data[data > 0]
        bins = list(10 ** (pd.Series(range(0, 6)).astype(float) / 1.2))
    fig = go.Figure(go.Histogram(
        x=data,
        nbinsx=40,
        marker=dict(color=COLORS["accent_teal"], line=dict(color=COLORS["bg_base"], width=0.5)),
        hovertemplate="Range: %{x}<br>Count: %{y}<extra></extra>",
    ))
    fig.update_layout(**_base(
        title=dict(text=title, font=dict(size=13)),
        height=280,
        xaxis=dict(type="log" if log_x else "linear"),
    ))
    return fig


def line_multi(df: pd.DataFrame, x: str, y: str, color: str, title: str = "") -> go.Figure:
    """Multi-series line chart: df has columns x, y, color."""
    if df.empty:
        return empty_fig()
    palette = CHART_LAYOUT["colorway"]
    fig = go.Figure()
    for i, (series_name, group) in enumerate(df.groupby(color)):
        clr = palette[i % len(palette)]
        fig.add_trace(go.Scatter(
            x=group[x], y=group[y], name=str(series_name),
            mode="lines+markers",
            line=dict(color=clr, width=2),
            marker=dict(size=5, color=clr),
            hovertemplate=f"{color}=%{{meta}}<br>Year=%{{x}}<br>Papers=%{{y}}<extra></extra>",
            meta=series_name,
        ))
    fig.update_layout(**_base(title=dict(text=title, font=dict(size=13)), height=320))
    return fig


def scatter(df: pd.DataFrame, x: str, y: str, size: str = None, text: str = None,
            title: str = "", color: str = COLORS["accent_cyan"]) -> go.Figure:
    if df.empty:
        return empty_fig()
    sizes = None
    if size and size in df.columns:
        s = df[size].fillna(0)
        sizes = (s / s.max() * 30 + 5).clip(5, 40).tolist()
    texts = df[text].tolist() if text and text in df.columns else None
    fig = go.Figure(go.Scatter(
        x=df[x], y=df[y],
        mode="markers+text" if texts else "markers",
        text=texts,
        textposition="top center",
        textfont=dict(size=9, color=COLORS["text_secondary"]),
        marker=dict(
            size=sizes if sizes else 10,
            color=df[x] if not sizes else df[size].fillna(0),
            colorscale=CYAN_SCALE,
            showscale=bool(sizes),
            line=dict(color=COLORS["bg_base"], width=1),
            opacity=0.8,
        ),
        hovertemplate=f"x=%{{x:,}}<br>y=%{{y:,}}<extra></extra>",
    ))
    fig.update_layout(**_base(title=dict(text=title, font=dict(size=13)), height=360))
    return fig


def heatmap(df: pd.DataFrame, x: str, y: str, z: str, title: str = "") -> go.Figure:
    if df.empty:
        return empty_fig()
    pivot = df.pivot_table(index=y, columns=x, values=z, aggfunc="sum").fillna(0)
    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=pivot.columns.tolist(),
        y=pivot.index.tolist(),
        colorscale=CYAN_SCALE,
        hovertemplate=f"{x}=%{{x}}<br>{y}=%{{y}}<br>Count=%{{z}}<extra></extra>",
    ))
    fig.update_layout(**_base(
        title=dict(text=title, font=dict(size=13)),
        height=max(280, len(pivot) * 22 + 60),
        xaxis=dict(tickfont=dict(size=10)),
        yaxis=dict(tickfont=dict(size=10)),
    ))
    return fig


def bar_v(df: pd.DataFrame, x: str, y: str, title: str = "", color_col: str = None) -> go.Figure:
    if df.empty:
        return empty_fig()
    marker = dict(color=COLORS["accent_cyan"])
    if color_col and color_col in df.columns:
        marker = dict(color=df[color_col], colorscale=CYAN_SCALE, showscale=False)
    fig = go.Figure(go.Bar(
        x=df[x], y=df[y],
        marker=marker,
        hovertemplate=f"{x}=%{{x}}<br>{y}=%{{y:,}}<extra></extra>",
    ))
    fig.update_layout(**_base(title=dict(text=title, font=dict(size=13)), height=300))
    return fig
