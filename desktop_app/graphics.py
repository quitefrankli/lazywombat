from typing import *
from plotly import express, graph_objects
from plotly.subplots import make_subplots
from pandas import DataFrame

from desktop_app.goal import Goal


def plot_velocity(goals: List[Goal]) -> None:
	goals = [goal for goal in goals if goal.state]
	goals.sort(key=lambda goal: goal.metadata.completion_date.timestamp())
	completion_dates = [goal.metadata.completion_date for goal in goals]

	first_completion_date = completion_dates[0]
	last_completion_date = completion_dates[-1]
	velocity = []
	for i in range(0, len(completion_dates)):
		completion_date = completion_dates[i]
		days_between = (completion_date - first_completion_date).days
		if days_between == 0:
			velocity.append(0)
			continue
		velocity.append(i/days_between)

	df = DataFrame(data={'completion_date': completion_dates, 'completions': range(len(completion_dates))})
	trace1 = graph_objects.Scatter(x=completion_dates, y=df['completions'], name='cumulative completions')
	trace2 = graph_objects.Scatter(x=completion_dates, y=velocity, name='completion velocity')
	# fig = px.line(data_frame=df,
	# 		      x='completion_date', 
	# 		      y='completions',
	# 			  title='Completion Velocity')
	fig = make_subplots(specs=[[{"secondary_y": True}]])
	fig.add_trace(trace1)
	fig.add_trace(trace2, secondary_y=True)
	fig['layout'].update(title='Goal Completion Velocity')
	fig.update_xaxes(title_text='date')
	fig.update_yaxes(title_text='cumulative completions', secondary_y=False)
	fig.update_yaxes(title_text='completion velocity (num/days)', secondary_y=True)
	fig.show()