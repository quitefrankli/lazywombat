import tkinter
import yaml
import datetime

from pathlib import Path
from datetime import datetime
from tkinter import ttk, Frame, Label, Button, Entry, Scrollbar, Canvas, Checkbutton
from typing import *
from goal import Goal
from client import Client


class DesktopApp(Frame):
    WIDTH = 500
    HEIGHT = 500
    # GOALS_CANVAS_WIDTH = WIDTH-10
    GOALS_CANVAS_WIDTH = WIDTH * 2/3
    GOAL_ENTRY_HEIGHT = 25
    MAX_GOALS = 30

    def __init__(self):
        super().__init__(None)
        self.client = Client()

        self.master.title("Todoist2")
        X = int((self.master.winfo_screenwidth() - self.WIDTH)/2)
        Y = int((self.master.winfo_screenheight() - self.HEIGHT)/2)
        self.master.geometry(f"{self.WIDTH}x{self.HEIGHT}+{X}+{Y}")
        Label(self, text="Goals").pack()

        self.canvas_frame = Frame(self)

        self.scrollbar = Scrollbar(self.canvas_frame)
        self.canvas = Canvas(self.canvas_frame, yscrollcommand=self.scrollbar.set)

        self.scrollbar.configure(command=self.canvas.yview)

        self.canvas.configure(scrollregion=(0, 
                                            0, 
                                            self.GOALS_CANVAS_WIDTH, 
                                            self.MAX_GOALS * self.GOAL_ENTRY_HEIGHT))

        self.refresh_goals_canvas()
            
        self.canvas.pack(side=tkinter.LEFT)
        self.scrollbar.pack(side=tkinter.LEFT, fill=tkinter.Y)
        self.canvas.config(width=self.GOALS_CANVAS_WIDTH, height=300)

        self.canvas_frame.pack()
        self.pack()

        Button(self,
               text="New Goal",
               command=self.spawn_new_goal_creation_window).pack(side=tkinter.LEFT)
        Button(self,
               text='Backup Goals',
               command=self.client.backup_goals).pack(side=tkinter.LEFT)

        self.master.bind('<Escape>', lambda _: self.quit())
        self.master.bind('n', lambda _: self.spawn_new_goal_creation_window())

    def refresh_goals_canvas(self) -> None:
        # first cleanup the canvas if there are existing goal windows
        self.canvas.delete(tkinter.ALL)

        goals = self.client.fetch_goals()
        for i in range(len(goals)):
            goal = goals[i]
            self.canvas.create_window(0, 
                                      i*self.GOAL_ENTRY_HEIGHT, 
                                      anchor=tkinter.NW,
                                      window=Label(self.canvas, text=goal.name))
            DELETE_BUTTON_OFFSET = 40
            CHECK_BOX_RIGHT_OFFSET = DELETE_BUTTON_OFFSET + self.GOAL_ENTRY_HEIGHT
            goal._tkinter_state = tkinter.BooleanVar(value=goal.state)
            self.canvas.create_window(self.GOALS_CANVAS_WIDTH-CHECK_BOX_RIGHT_OFFSET,
                                      i*self.GOAL_ENTRY_HEIGHT,
                                      anchor=tkinter.NW,
                                      window=Checkbutton(self.canvas,
                                                         variable=goal._tkinter_state,
                                                         onvalue=True,
                                                         offvalue=False,
                                                         command=lambda idx=i: self.client.toggle_goal_state(idx)))
            self.canvas.create_window(self.GOALS_CANVAS_WIDTH-DELETE_BUTTON_OFFSET,
                                      i*self.GOAL_ENTRY_HEIGHT,
                                      anchor=tkinter.NW,
                                      window=Button(self.canvas,
                                                    text='delete',
                                                    command=lambda idx=i: self.delete_goal(idx)))

    def delete_goal(self, idx: int) -> None:
        self.client.delete_goal(idx)
        self.refresh_goals_canvas()

    def spawn_new_goal_creation_window(self):
        top = tkinter.Toplevel(self)
        top.geometry(f"200x200+{self.master.winfo_x()}+{self.master.winfo_y()}")
        top.title("New Goal")
        goal_name = tkinter.StringVar()
        Label(top, text='Enter New Goal').pack()
        new_goal_entry = Entry(top, textvariable=goal_name)
        new_goal_entry.focus()
        new_goal_entry.pack()

        def close_window():
            if goal_name.get():
                self.client.add_goal(Goal(goal_name.get(), False))
                self.refresh_goals_canvas()
            top.destroy()
        top.bind('<Return>', lambda _: close_window())
        top.bind('<Escape>', lambda _: top.destroy())
        Button(top, text="Ok", command=close_window).pack()
        
    def run(self) -> None:
        self.mainloop()

def main() -> None:
    app = DesktopApp()
    app.run()

if __name__ == '__main__':
    main()