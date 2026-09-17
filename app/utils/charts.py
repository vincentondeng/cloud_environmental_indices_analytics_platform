"""
Renders a time-series chart as Plotly JSON.
"""
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio


def build_timeseries_chart(df: pd.DataFrame, index_name: str, trend_per_year: float | None) -> str:
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["value"],
            mode="lines+markers",
            name=index_name,
            line=dict(width=2),
            marker=dict(size=6),
        )
    )

    if trend_per_year is not None:
        valid = df.dropna(subset=["value"])
        t_years = ((valid.index - valid.index[0]).days / 365.25).to_numpy()
        fitted = valid["value"].mean() - trend_per_year * t_years.mean() + trend_per_year * t_years
        fig.add_trace(
            go.Scatter(
                x=valid.index,
                y=fitted,
                mode="lines",
                name=f"trend ({trend_per_year:+.4f}/yr)",
                line=dict(dash="dash", width=1.5),
            )
        )

    fig.update_layout(
        title=f"{index_name} time series",
        xaxis_title="Date",
        yaxis_title=index_name,
        template="plotly_white",
        height=400,
        margin=dict(l=40, r=20, t=50, b=40),
    )

    return pio.to_json(fig)
