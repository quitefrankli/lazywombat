import tkinter

from tkinter import ttk, Frame, Label, Button, Entry, Scrollbar, Canvas, Checkbutton, messagebox
from tkinter.scrolledtext import ScrolledText
from typing import *
from playsound import playsound
from abc import abstractmethod

from desktop_app.goal import Goal
from desktop_app.client import ClientV2


class GoalsWindow(Frame):
    WIDTH = 500
    HEIGHT = 500
    # GOALS_CANVAS_WIDTH = WIDTH-10
    GOALS_CANVAS_WIDTH = WIDTH * 4/5
    GOALS_CANVAS_HEIGHT = HEIGHT * 3/4
    GOAL_ENTRY_HEIGHT = 25
    MAX_GOALS = 80
    GOAL_ENTRY_FONT = 'Helvetica 9'

    def __init__(self, master: Any, goals_summary: str, client: ClientV2, refresh_all: Callable):
        super().__init__(master)
        self.client = client
        self.refresh_all = refresh_all

        title = Label(self, text=goals_summary, font='Helvetica 12 bold')
        self.canvas_frame = Frame(self)
        self.canvas_frame.configure(highlightthickness=2, highlightbackground="black")
        self.scrollbar = Scrollbar(self.canvas_frame)
        self.canvas = Canvas(self.canvas_frame, yscrollcommand=self.scrollbar.set)

        self.scrollbar.configure(command=self.canvas.yview)
        self.canvas.configure(scrollregion=(0, 
                                            0, 
                                            self.GOALS_CANVAS_WIDTH, 
                                            self.MAX_GOALS * self.GOAL_ENTRY_HEIGHT))
        title.pack(side=tkinter.TOP)
        self.canvas.pack(side=tkinter.LEFT)
        self.scrollbar.pack(side=tkinter.LEFT, fill=tkinter.Y)
        self.canvas.config(width=self.GOALS_CANVAS_WIDTH, height=self.GOALS_CANVAS_HEIGHT)
        self.canvas_frame.pack(side=tkinter.TOP)

    def setup_parent(self, child_id: int, parent_id: int) -> None:
        self.client.setup_parent(child_id, parent_id)
        self.refresh_goals_canvas()

    def delete_goal(self, goal_id: int) -> None:
        self.client.delete_goal(goal_id)
        self.refresh_all_goal_windows()

    @abstractmethod
    def refresh_goals_canvas(self) -> None:
        pass

    def refresh_all_goal_windows(self) -> None:
        self.refresh_all()

    class GoalRenderer:
        GOAL_ENTRY_HEIGHT = 25

        def __init__(self, 
                     goal_constructor: Callable, 
                     goal_fetcher: Callable, 
                     y_offset: int, 
                     ignore_children: bool):
            self.goal_fetcher = goal_fetcher
            self.goal_constructor = goal_constructor
            self.y_offset = y_offset
            self.x_offset = 0
            self.ignore_children = ignore_children

        # returns the number of goals processed
        def render_goal(self, goal: Goal) -> int:
            self.goal_constructor(goal, self.x_offset, self.y_offset * self.GOAL_ENTRY_HEIGHT)
            self.y_offset += 1

            return self.render_children(goal) + 1

        def render_children(self, parent: Goal) -> int:
            if self.ignore_children or parent.collapsed:
                return 0
            self.x_offset += 20
            num_goals_processed = 0
            for child_id in parent.children:
                child = self.goal_fetcher(child_id)
                num_goals_processed += self.render_goal(child)
            self.x_offset -= 20

            return num_goals_processed