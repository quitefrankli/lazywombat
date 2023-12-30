import tkinter

from tkinter import ttk, Frame, Label, Button, Entry, Scrollbar, Canvas, Checkbutton, messagebox
from typing import *
from playsound import playsound

from desktop_app import graphics
from desktop_app.goal import Goal
from desktop_app.client import ClientV2
from desktop_app.app_windows.summary_goals import SummaryGoals
from desktop_app.app_windows.daily_goals import DailyGoals
from desktop_app.app_windows.backlogged_goals import BackloggedGoals
from desktop_app.app_windows.completed_goals import CompletedGoals


class DesktopApp(Frame):
    WIDTH = 1000
    HEIGHT = 500
    # GOALS_CANVAS_WIDTH = WIDTH-10
    GOALS_CANVAS_WIDTH = WIDTH * 4/5
    GOALS_CANVAS_HEIGHT = HEIGHT * 3/4
    GOAL_ENTRY_HEIGHT = 25
    MAX_GOALS = 80
    GOAL_ENTRY_FONT = 'Helvetica 9'

    def __init__(self, debug: bool = False):
        super().__init__(None)
        self.client = ClientV2(debug=debug)

        debug_warning = f"{'(DEBUG MODE CHANGES NOT PUSHED) ' if debug else ''}"
        self.master.title(f"{debug_warning}Todoist2")
        X = int((self.master.winfo_screenwidth() - self.WIDTH)/2)
        Y = int((self.master.winfo_screenheight() - self.HEIGHT)/2)
        self.master.geometry(f"{self.WIDTH}x{self.HEIGHT}+{X}+{Y}")
        Label(self, text=f"{debug_warning}Goals", font='Helvetica 18 bold').pack()

        self.goals_frame = Frame(self)
        self.summary_goals = SummaryGoals(self.goals_frame, self.client, self.refresh_all_goals_windows)
        self.summary_goals.pack(side=tkinter.LEFT)

        self.daily_goals = DailyGoals(self.goals_frame, self.client, self.refresh_all_goals_windows)
        self.daily_goals.pack(side=tkinter.TOP, padx=15)
        self.goals_frame.pack()

        self.backlogged_goals = BackloggedGoals(self.goals_frame, self.client, self.refresh_all_goals_windows)
        self.completed_goals = CompletedGoals(self.goals_frame, self.client, self.refresh_all_goals_windows)

        self.backlogged_goals.toggle_hidden()
        self.completed_goals.toggle_hidden()

        self.pack()

        self.new_goals_button = Button(self,
                                       text="New Goal",
                                       command=self.spawn_new_goal_creation_window)
        self.backup_goals_button = Button(self,
                                          text='Backup Goals',
                                          command=self.backup_goals)
        self.show_velocity_button = Button(self,
                                           text='Show Velocity',
                                           command=lambda: graphics.plot_velocity(self.client.fetch_goals()))
        self.toggle_backlog_button = Button(self,
               text='Toggle Backlog',
               command=self.toggle_backlog)
        self.toggle_completed_button = Button(self,
               text="Toggle Completed",
               command=self.toggle_completed)
        self.is_abort = False
        def abort():
            self.is_abort = True
            self.quit()
        # exit without saving
        self.abort_button = Button(self,
                                   text="Abort",
                                   command=abort)
        self.abort_button.pack(side=tkinter.RIGHT)

        self.refresh_all_goals_windows()
        self.refresh_all_buttons()

        self.master.bind('<Escape>', lambda _: self.quit())
        # self.master.bind('n', lambda _: self.spawn_new_goal_creation_window())

    def refresh_all_goals_windows(self) -> None:
        if not self.backlogged_goals.hidden:
            self.backlogged_goals.refresh_goals_canvas()
        elif not self.completed_goals.hidden:
            self.completed_goals.refresh_goals_canvas()
        else:
            self.summary_goals.refresh_goals_canvas()
            self.daily_goals.refresh_goals_canvas()

    def refresh_all_buttons(self) -> None:
        self.new_goals_button.pack_forget()
        self.backup_goals_button.pack_forget()
        self.show_velocity_button.pack_forget()
        self.toggle_backlog_button.pack_forget()
        self.toggle_completed_button.pack_forget()
        if not self.backlogged_goals.hidden:
            self.toggle_backlog_button.pack(side=tkinter.LEFT)
        elif not self.completed_goals.hidden:
            self.toggle_completed_button.pack(side=tkinter.LEFT)
        else:
            self.new_goals_button.pack(side=tkinter.LEFT)
            self.backup_goals_button.pack(side=tkinter.LEFT)
            self.show_velocity_button.pack(side=tkinter.LEFT)
            self.toggle_backlog_button.pack(side=tkinter.LEFT)
            self.toggle_completed_button.pack(side=tkinter.LEFT)

    def toggle_completed(self) -> None:
        self.completed_goals.toggle_hidden()
        if self.completed_goals.hidden:
            self.completed_goals.pack_forget()
            self.summary_goals.pack(side=tkinter.LEFT)
            self.daily_goals.pack(side=tkinter.TOP, padx=15)
        else:
            self.summary_goals.pack_forget()
            self.daily_goals.pack_forget()
            self.completed_goals.pack(side=tkinter.LEFT)
        self.refresh_all_buttons()
        self.refresh_all_goals_windows()

    def toggle_backlog(self) -> None:
        self.backlogged_goals.toggle_hidden()
        if self.backlogged_goals.hidden:
            self.backlogged_goals.pack_forget()
            self.summary_goals.pack(side=tkinter.LEFT)
            self.daily_goals.pack(side=tkinter.TOP, padx=15)
        else:
            self.summary_goals.pack_forget()
            self.daily_goals.pack_forget()
            self.backlogged_goals.pack(side=tkinter.LEFT)
        self.refresh_all_buttons()
        self.refresh_all_goals_windows()

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
                self.client.add_goal(Goal(id=-1, name=goal_name.get(), state=False))
                playsound('data/fx2.wav', block=False)
                self.refresh_all_goals_windows()
            top.destroy()
        top.bind('<Return>', lambda _: close_window())
        top.bind('<Escape>', lambda _: top.destroy())
        Button(top, text="Ok", command=close_window).pack()
        
    def backup_goals(self) -> None:
        backup = self.client.backup_goals()
        messagebox.showinfo(message=f'Created backup: {backup}')

    def run(self) -> None:
        self.mainloop()
        if not self.is_abort:
            self.client.save_goals()