from typing import * # type: ignore
from plotly import express, graph_objects
from pandas import DataFrame

from web_app.metrics.app_data import Metric


def plot_metric(metric: Metric) -> str:
    if not metric.data:
        raise RuntimeError("No data to plot")

    data = metric.data
    data.sort(key=lambda x: x.date)
    dates = [point.date for point in data]
    values = [point.value for point in data]

    df = DataFrame(data={'date': dates, 'value': values})
    trace = graph_objects.Scatter(x=dates, y=values, mode='lines+markers', name=metric.name)
    fig = express.line(df, x='date', y='value', title=metric.name)
    fig.add_trace(trace)
    fig.update_xaxes(title_text='Date')
    fig.update_yaxes(title_text=metric.unit)
    fig.update_layout(showlegend=False)
    
    return fig.to_html(full_html=False)
