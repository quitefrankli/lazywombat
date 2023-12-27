import tkinter

from tkinter import ttk, Frame, Label, Button, Entry, Scrollbar, Canvas, Checkbutton, messagebox
from typing import *
from playsound import playsound

from desktop_app import graphics
from desktop_app.goal import Goal
from desktop_app.client import ClientV2
from desktop_app.app_windows.normal_goals import NormalGoals
from desktop_app.app_windows.daily_goals import DailyGoals


class DesktopApp(Frame):
    WIDTH = 1000
    HEIGHT = 500
    # GOALS_CANVAS_WIDTH = WIDTH-10
    GOALS_CANVAS_WIDTH = WIDTH * 4/5
    GOALS_CANVAS_HEIGHT = HEIGHT * 3/4
    GOAL_ENTRY_HEIGHT = 25
    MAX_GOALS = 80
    GOAL_ENTRY_FONT = 'Helvetica 9'

    def __init__(self):
        super().__init__(None)
        self.client = ClientV2()

        self.master.title("Todoist2")
        X = int((self.master.winfo_screenwidth() - self.WIDTH)/2)
        Y = int((self.master.winfo_screenheight() - self.HEIGHT)/2)
        self.master.geometry(f"{self.WIDTH}x{self.HEIGHT}+{X}+{Y}")
        Label(self, text="Goals", font='Helvetica 18 bold').pack()

        self.goals_frame = Frame(self)
        self.normal_goals = NormalGoals(self.goals_frame, self.client, self.refresh_goals_canvas)
        self.normal_goals.pack(side=tkinter.LEFT)

        self.daily_goals = DailyGoals(self.goals_frame, self.client, self.refresh_goals_canvas)
        self.daily_goals.pack(side=tkinter.TOP, padx=15)
        self.goals_frame.pack()

        self.refresh_goals_canvas()
        self.pack()

        Button(self,
               text="New Goal",
               command=self.spawn_new_goal_creation_window).pack(side=tkinter.LEFT)
        # Button(self,
        #        text='Save Goals',
        #        command=self.client.save_goals).pack(side=tkinter.LEFT)
        Button(self,
               text='Backup Goals',
               command=self.backup_goals).pack(side=tkinter.LEFT)
        Button(self,
               text='Show Velocity',
               command=lambda: graphics.plot_velocity(self.client.fetch_goals())).pack(side=tkinter.LEFT)

        self.master.bind('<Escape>', lambda _: self.quit())
        self.master.bind('n', lambda _: self.spawn_new_goal_creation_window())

    def refresh_goals_canvas(self) -> None:
        self.normal_goals.refresh_goals_canvas()
        self.daily_goals.refresh_goals_canvas()
        
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
                self.client.add_goal(Goal(-1, goal_name.get(), False))
                self.refresh_goals_canvas()
            top.destroy()
        top.bind('<Return>', lambda _: close_window())
        top.bind('<Escape>', lambda _: top.destroy())
        Button(top, text="Ok", command=close_window).pack()
        
    def backup_goals(self) -> None:
        backup = self.client.backup_goals()
        messagebox.showinfo(message=f'Created backup: {backup}')

    def run(self) -> None:
        self.mainloop()
        self.client.save_goals()

def main() -> None:
    app = DesktopApp()
    app.run()

if __name__ == '__main__':
    main()