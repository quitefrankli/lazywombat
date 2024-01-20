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
    class DetailsWindow(GoalDetailsWindow):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.remove_from_daily_button.pack_forget()
            self.remove_from_backlog_button.pack_forget()
            
            self.create_child_goal_button = Button(self.custom_buttons_frame,
                                                   text='Create Child Goal',
                                                   command=self.create_child_goal)
            self.create_child_goal_button.pack(padx=1, side=tkinter.LEFT)

        def create_child_goal(self):
            top = tkinter.Toplevel(self)
            top.geometry(f"200x200+{self.winfo_rootx()}+{self.winfo_rooty()}")
            top.title("New Child Goal")
            goal_name = tkinter.StringVar()
            Label(top, text='Enter New Goal').pack()
            new_goal_entry = Entry(top, textvariable=goal_name)
            new_goal_entry.focus()
            new_goal_entry.pack()
            def close_window():
                if goal_name.get():
                    new_goal_id = self.client.add_goal(Goal(id=-1, name=goal_name.get(), state=False))
                    self.goals_window.setup_parent(new_goal_id, self.goal.id)
                    playsound('data/fx2.wav', block=False)
                    self.refresh_all()
                top.destroy()
            top.bind('<Return>', lambda _: close_window())
            top.bind('<Escape>', lambda _: top.destroy())
            Button(top, text="Ok", command=close_window).pack()

    def __init__(self, master: Any, client: ClientV2, refresh_all: Callable):
        super().__init__(master, 'Goals Summary', client, refresh_all)

    def refresh_goals_canvas(self) -> None:
        # first cleanup the canvas if there are existing goal windows
        self.canvas.delete(tkinter.ALL)
        
        goals = self.client.fetch_goals()
        now = datetime.now()
        def should_render(goal: Goal) -> bool:
            # reserve rendering of children to later
            if goal.parent != -1:
                return False
            if goal.backlogged:
                return False
            if goal.repeat:
                return False
            # hides goals that have been completed for a while
            days_since_completion = 0 if not goal.state else (now - goal.metadata.completion_date).days
            if days_since_completion > 2:
                return False
            
            return True
        
        goals = [goal for goal in goals if should_render(goal)]
        goals.sort(key=lambda goal: goal.metadata.creation_date.timestamp())
        idx_offset: int = 0
        last_date_label = Goal.NULL_DATE.date()

        def get_y_val() -> int:
            return idx_offset * self.GOAL_ENTRY_HEIGHT
        
        for goal in goals:
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