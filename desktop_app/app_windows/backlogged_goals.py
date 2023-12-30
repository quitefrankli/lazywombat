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


class BackloggedGoals(GoalsWindow):
    def __init__(self, master: Any, client: ClientV2, refresh_all: Callable):
        super().__init__(master, 'Backlogged Goals', client, refresh_all)

    class DetailsWindow(GoalDetailsWindow):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.make_daily_button.pack_forget()
            self.backglog_button.pack_forget()
            self.unparent_button.pack_forget()
            self.remove_from_daily_button.pack_forget()

    def refresh_goals_canvas(self) -> None:
        # first cleanup the canvas if there are existing goal windows
        self.canvas.delete(tkinter.ALL)
        goals = self.client.fetch_goals()
        goals.sort(key=lambda goal: goal.metadata.creation_date.timestamp())
        idx_offset = 0

        for goal in goals:
            if not goal.backlogged or goal.state:
                continue

            def goal_constructor(goal: Goal, x: int, y: int) -> GoalWidget:
                goal_widget = GoalWidget(self.canvas, 
                                         self, 
                                         goal, 
                                         self.client, 
                                         x, 
                                         y, 
                                         details_window_cls=self.DetailsWindow,
                                         disable_dragging=True)
                self.canvas.create_window(0, y, anchor=tkinter.NW, window=goal_widget)
                return goal_widget
            renderer = self.GoalRenderer(goal_constructor, 
                                         self.client.fetch_goal, 
                                         idx_offset,
                                         ignore_children=True)
            idx_offset += renderer.render_goal(goal)