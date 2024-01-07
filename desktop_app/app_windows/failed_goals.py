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


class FailedGoals(GoalsWindow):
    def __init__(self, master: Any, client: ClientV2, refresh_all: Callable):
        super().__init__(master, 'Failed Goals', client, refresh_all)

    class DetailsWindow(GoalDetailsWindow):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.backglog_button.pack_forget()
            self.unparent_button.pack_forget()
            self.remove_from_backlog_button.pack_forget()
            self.remove_from_daily_button.pack_forget()

        def toggle_daily(self) -> None:
            super().toggle_daily()
            self.goals_window.refresh_goals_canvas()

    class FailedGoalWidget(GoalWidget):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

        def construct_row(self):
            x_padding = 2

            label_frame = Frame(self, width=self.GOALS_CANVAS_WIDTH-50, height=self.GOAL_ENTRY_HEIGHT)
            label_frame.pack(side=tkinter.LEFT)
            label_frame.pack_propagate(False) # Stops child widgets of label_frame from resizing it
            
            date_window = Label(label_frame,
                                text=self.goal.daily.strftime('%d/%m/%Y'),
                                font=self.GOAL_ENTRY_FONT,
                                anchor='e',
                                fg='red')
            date_window.pack(side=tkinter.LEFT, padx=x_padding)
            label_window = Label(label_frame, 
                                text=self.goal.name, 
                                font=self.GOAL_ENTRY_FONT, 
                                anchor='w', 
                                name=str(self.goal.id))
            label_window.pack(side=tkinter.LEFT)

            label_window.bind('<Button-1>', self.on_lclick)
            
    def refresh_goals_canvas(self) -> None:
        # first cleanup the canvas if there are existing goal windows
        self.canvas.delete(tkinter.ALL)
        goals = self.client.get_failed_goals()
        goals.sort(key=lambda goal: goal.daily.timestamp())
        idx_offset = 0

        for goal in goals:
            def goal_constructor(goal: Goal, x: int, y: int) -> GoalWidget:
                goal_widget = self.FailedGoalWidget(self.canvas, 
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