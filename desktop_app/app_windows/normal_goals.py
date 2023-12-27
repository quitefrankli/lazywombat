import tkinter
import datetime

from datetime import datetime
from tkinter import ttk, Frame, Label, Button, Entry, Scrollbar, Canvas, Checkbutton, messagebox
from tkinter.scrolledtext import ScrolledText
from typing import *
from playsound import playsound

from desktop_app.goal import Goal
from desktop_app.client import ClientV2


class NormalGoals(Frame):
    WIDTH = 500
    HEIGHT = 500
    # GOALS_CANVAS_WIDTH = WIDTH-10
    GOALS_CANVAS_WIDTH = WIDTH * 4/5
    GOALS_CANVAS_HEIGHT = HEIGHT * 3/4
    GOAL_ENTRY_HEIGHT = 25
    MAX_GOALS = 80
    GOAL_ENTRY_FONT = 'Helvetica 9'

    def __init__(self, master: Any, client: ClientV2, refresh_all):
        super().__init__(master)
        self.client = client
        self.refresh_all = refresh_all

        title = Label(self, text='Normal Goals', font='Helvetica 12 bold')
        self.canvas_frame = Frame(self)
        self.scrollbar = Scrollbar(self.canvas_frame)
        self.canvas = Canvas(self.canvas_frame, yscrollcommand=self.scrollbar.set)

        self.scrollbar.configure(command=self.canvas.yview)
        self.canvas.configure(scrollregion=(0, 
                                            0, 
                                            self.GOALS_CANVAS_WIDTH, 
                                            self.MAX_GOALS * self.GOAL_ENTRY_HEIGHT),
                              highlightthickness=2, highlightbackground="black")
        title.pack(side=tkinter.TOP)
        self.canvas.pack(side=tkinter.LEFT)
        self.scrollbar.pack(side=tkinter.LEFT, fill=tkinter.Y)
        self.canvas.config(width=self.GOALS_CANVAS_WIDTH, height=self.GOALS_CANVAS_HEIGHT)
        self.canvas_frame.pack(side=tkinter.TOP)

        self._show_completed = False
        def toggle_completed():
            self._show_completed = not self._show_completed
            self.refresh_goals_canvas()
        Button(self,
               text='Toggle Completed',
               command=toggle_completed).pack(side=tkinter.BOTTOM)
        
    def refresh_goals_canvas(self) -> None:
        # first cleanup the canvas if there are existing goal windows
        self.canvas.delete(tkinter.ALL)
        goals = self.client.fetch_goals()
        goals.sort(key=lambda goal: goal.metadata.creation_date.timestamp())
        idx_offset = 0
        x_padding = 2
        y_padding = 2
        last_date_label = Goal.NULL_DATE.date()
        now = datetime.now()

        def get_y_val() -> int:
            return idx_offset * self.GOAL_ENTRY_HEIGHT
        
        for goal in goals:
            if goal.daily.date() == now.date():
                continue
            if self._show_completed and not goal.state:
                continue
            days_since_completion = 0 if not goal.state else (now - goal.metadata.completion_date).days
            if not self._show_completed and days_since_completion > 3:
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
            label_window = Label(self.canvas, text=goal.name, font=self.GOAL_ENTRY_FONT)
            self.canvas.create_window(0, 
                                      get_y_val(), 
                                      anchor=tkinter.NW,
                                      window=label_window)
            label_window.bind('<Double-Button-1>', lambda _, goal_id=goal.id: self.spawn_goal_details_window(goal_id))
            DELETE_BUTTON_OFFSET = 40
            CHECK_BOX_RIGHT_OFFSET = DELETE_BUTTON_OFFSET + self.GOAL_ENTRY_HEIGHT
            goal._tkinter_state = tkinter.BooleanVar(value=goal.state)
            def toggle_goal_state(goal_id: int):
                playsound('data/fx1.wav', False)
                self.client.toggle_goal_state(goal_id)
            self.canvas.create_window(self.GOALS_CANVAS_WIDTH-CHECK_BOX_RIGHT_OFFSET-y_padding,
                                      get_y_val(),
                                      anchor=tkinter.NW,
                                      window=Checkbutton(self.canvas,
                                                         variable=goal._tkinter_state,
                                                         onvalue=True,
                                                         offvalue=False,
                                                         command=lambda goal_id=goal.id: toggle_goal_state(goal_id)))
            self.canvas.create_window(self.GOALS_CANVAS_WIDTH-DELETE_BUTTON_OFFSET-y_padding,
                                      get_y_val(),
                                      anchor=tkinter.NW,
                                      window=Button(self.canvas,
                                                    text='delete',
                                                    command=lambda goal_id=goal.id: self.delete_goal(goal_id)))
            idx_offset+=1
            
    def delete_goal(self, idx: int) -> None:
        self.client.delete_goal(idx)
        self.refresh_goals_canvas()

    def spawn_goal_details_window(self, goal_id: int) -> None:
        top = tkinter.Toplevel(self)
        top.geometry(f"400x200+{self.master.winfo_rootx()}+{self.master.winfo_rooty()}")
        top.title("Goal Details")
        goal = self.client.fetch_goal(goal_id)
        Label(top, text=f"Goal Name: {goal.name}").pack()
        description = ScrolledText(top, wrap=tkinter.WORD, width=40, height=6)
        description.pack()
        description.insert(tkinter.INSERT, goal.description)
        description.focus()
        def close_window():
            new_description = description.get("1.0", tkinter.END)
            if goal.description != new_description:
                self.client.need_saving = True
                goal.description = new_description
            top.destroy()
        top.bind('<Escape>', lambda _: top.destroy())
        Button(top, text="Ok", command=close_window).pack()
        is_daily = goal.daily.date() == datetime.now().date()
        def toggle_daily():
            self.client.toggle_goal_daily(goal_id)
            self.refresh_all()
            close_window()
        Button(top, 
               text="Make Normal" if is_daily else "Make Daily", 
               command=toggle_daily).pack(side=tkinter.LEFT)