import tkinter

from datetime import datetime
from tkinter import ttk, Frame, Label, Button, Entry, Scrollbar, Canvas, Checkbutton, messagebox
from tkinter.scrolledtext import ScrolledText
from typing import *
from playsound import playsound

from desktop_app.goal import Goal
from desktop_app.client import ClientV2
from .goal_widget import GoalWidget, GoalDetailsWindow
from .goals_window import GoalsWindow


class SummaryGoals(GoalsWindow):
    def __init__(self, master: Any, client: ClientV2, refresh_all: Callable):
        super().__init__(master, 'Goals Summary', client, refresh_all)

    class DetailsWindow(GoalDetailsWindow):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.remove_from_daily_button.pack_forget()
            self.remove_from_backlog_button.pack_forget()

    def refresh_goals_canvas(self) -> None:
        # first cleanup the canvas if there are existing goal windows
        self.canvas.delete(tkinter.ALL)
        
        goals = self.client.fetch_goals()
        goals.sort(key=lambda goal: goal.metadata.creation_date.timestamp())
        idx_offset: int = 0
        last_date_label = Goal.NULL_DATE.date()
        now = datetime.now()

        def get_y_val() -> int:
            return idx_offset * self.GOAL_ENTRY_HEIGHT
        
        for goal in goals:
            if goal.backlogged:
                continue
            has_children = goal.children
			# shows goals that are recently completed
            days_since_completion = 0 if not goal.state else (now - goal.metadata.completion_date).days
            if not has_children and days_since_completion > 3:
                continue

            # reserve rendering of children to later
            if goal.parent != -1:
                continue

            goal_date = goal.metadata.creation_date.date()
            if last_date_label != goal_date:
                last_date_label = goal_date
                self.canvas.create_window(self.GOALS_CANVAS_WIDTH/2,
                                          get_y_val(),
                                          anchor=tkinter.N,
                                          window=Label(self.canvas, 
                                                       text=last_date_label.strftime("%d/%m/%Y"),
                                                       font=f'{self.GOAL_ENTRY_FONT} bold'))
                idx_offset += 1

            def goal_constructor(goal: Goal, x: int, y: int) -> GoalWidget:
                goal_widget = GoalWidget(self.canvas, 
                                         self, 
                                         goal, 
                                         self.client, 
                                         x, 
                                         y, 
                                         details_window_cls=self.DetailsWindow,
                                         disable_dragging=False)
                self.canvas.create_window(0, y, anchor=tkinter.NW, window=goal_widget)
                return goal_widget
            renderer = self.GoalRenderer(goal_constructor, 
                                         self.client.fetch_goal, 
                                         idx_offset,
                                         ignore_children=False)
            idx_offset += renderer.render_goal(goal)