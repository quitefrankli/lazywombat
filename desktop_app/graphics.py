from queue import Queue
from typing import *
from plotly import express, graph_objects
from plotly.subplots import make_subplots
from pandas import DataFrame
from datetime import timedelta, datetime
from scipy.signal import savgol_filter

from desktop_app.goal import Goal


def get_immediate_monday(date: datetime) -> datetime:
    monday = date - timedelta(days=date.weekday())
    return monday

# returns a tuple containing a list of the number of completions per week and a list of the dates of the end of each week
def get_completions_per_week(completions: List[datetime]) -> Tuple[List[int], List[int]]:
    first_monday = get_immediate_monday(completions[0])
    last_monday = get_immediate_monday(completions[-1])

    weeks = []
    completions_per_week = []

    num_days_in_week = 3

    curr_monday = first_monday
    completion_idx = 0
    while curr_monday < last_monday:
        next_monday = curr_monday + timedelta(days=num_days_in_week)
        completions_in_curr_week = 0
        while completion_idx < len(completions) and completions[completion_idx] < next_monday:
            completion_idx += 1
            completions_in_curr_week += 1
        weeks.append(curr_monday)
        completions_per_week.append(completions_in_curr_week)
        curr_monday = next_monday

    return completions_per_week, weeks

def calculate_moving_averages(completions_per_week: List[int]) -> List[float]:
    averages = []
    window_size = 3
    for i in range(len(completions_per_week)):
        if i < window_size:
            averages.append(0)
        averages.append(sum(completions_per_week[i:i+window_size]) / float(window_size))
    return averages

def apply_smoothening(completions_per_week: List[int]) -> List[float]:
    window_size = 3
    polynomial_order = 1
    filtered = savgol_filter(completions_per_week, window_size, polynomial_order)
    return filtered

def plot_velocity(goals: List[Goal]) -> None:
    goals = [goal for goal in goals if goal.state]
    goals.sort(key=lambda goal: goal.metadata.completion_date.timestamp())
    completion_dates = [goal.metadata.completion_date for goal in goals]

    completions_per_week, weeks = get_completions_per_week(completion_dates)
    moving_averages = calculate_moving_averages(completions_per_week)
    smoothened = apply_smoothening(completions_per_week)

    df = DataFrame(data={'completion_date': completion_dates, 'completions': range(len(completion_dates))})
    trace1 = graph_objects.Scatter(x=completion_dates, 
                                   y=df['completions'], 
                                   name='cumulative completions',
                                   line=dict(color='red'))
    trace2 = graph_objects.Scatter(x=weeks, 
                                   y=completions_per_week, 
                                   name='completion velocity', 
                                   line=dict(dash='dot', color='black'))
    trace3 = graph_objects.Scatter(x=weeks, y=moving_averages, name='moving average')
    trace4 = graph_objects.Scatter(x=weeks, 
                                   y=smoothened, 
                                   name='smoothened',
                                   line=dict(color='blue', 
                                             shape='spline',
                                             smoothing=1.3))
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(trace1)
    fig.add_trace(trace2, secondary_y=True)
    # fig.add_trace(trace3, secondary_y=True)
    fig.add_trace(trace4, secondary_y=True)
    fig['layout'].update(title='Goal Completion Velocity')
    fig.update_xaxes(title_text='date')
    fig.update_yaxes(title_text='cumulative completions', secondary_y=False)
    fig.update_yaxes(title_text='completion velocity (num/days)', secondary_y=True)
    fig.update_layout(yaxis2=dict(range=[0, max(completions_per_week)]))
    fig.show()