import tkinter
import yaml
import datetime
import boto3

from pathlib import Path
from datetime import datetime, timedelta
from tkinter import ttk, Frame, Label, Button, Entry, Scrollbar, Canvas, Checkbutton, messagebox
from typing import *
from goal import Goal
from client import ClientV2


class DesktopApp(Frame):
    WIDTH = 500
    HEIGHT = 500
    # GOALS_CANVAS_WIDTH = WIDTH-10
    GOALS_CANVAS_WIDTH = WIDTH * 4/5
    GOALS_CANVAS_HEIGHT = HEIGHT * 3/4
    GOAL_ENTRY_HEIGHT = 25
    MAX_GOALS = 30
    GOAL_ENTRY_FONT = 'Helvetica 9'

    def __init__(self):
        super().__init__(None)
        self.client = ClientV2()
        self._show_completed = False

        self.master.title("Todoist2")
        X = int((self.master.winfo_screenwidth() - self.WIDTH)/2)
        Y = int((self.master.winfo_screenheight() - self.HEIGHT)/2)
        self.master.geometry(f"{self.WIDTH}x{self.HEIGHT}+{X}+{Y}")
        Label(self, text="Goals", font='Helvetica 18 bold').pack()

        self.canvas_frame = Frame(self)

        self.scrollbar = Scrollbar(self.canvas_frame)
        self.canvas = Canvas(self.canvas_frame, yscrollcommand=self.scrollbar.set)

        self.scrollbar.configure(command=self.canvas.yview)

        self.canvas.configure(scrollregion=(0, 
                                            0, 
                                            self.GOALS_CANVAS_WIDTH, 
                                            self.MAX_GOALS * self.GOAL_ENTRY_HEIGHT),
                              highlightthickness=2, highlightbackground="black")

        self.refresh_goals_canvas()
            
        self.canvas.pack(side=tkinter.LEFT)
        self.scrollbar.pack(side=tkinter.LEFT, fill=tkinter.Y)
        self.canvas.config(width=self.GOALS_CANVAS_WIDTH, height=self.GOALS_CANVAS_HEIGHT)

        self.canvas_frame.pack()
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
        def toggle_completed():
            self._show_completed = not self._show_completed
            self.refresh_goals_canvas()
        Button(self,
               text='Toggle Completed',
               command=toggle_completed).pack(side=tkinter.LEFT)

        self.master.bind('<Escape>', lambda _: self.quit())
        self.master.bind('n', lambda _: self.spawn_new_goal_creation_window())

    def refresh_goals_canvas(self) -> None:
        # first cleanup the canvas if there are existing goal windows
        self.canvas.delete(tkinter.ALL)
        goals = self.client.fetch_goals()
        goals.sort(key=lambda goal: goal.metadata.creation_date.timestamp())
        offset = 0
        x_padding = 2
        y_padding = 2
        last_date_label = datetime(year=2000, month=1, day=1).date()
        now = datetime.now()
        for i in range(len(goals)):
            def get_y_val() -> int:
                return (i + offset) * self.GOAL_ENTRY_HEIGHT
            goal = goals[i]
            days_since_completion = 0 if not goal.state else (now - goal.metadata.completion_date).days
            if (self._show_completed and not goal.state) or (not self._show_completed and days_since_completion > 3):
                offset -= 1
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
                offset += 1
            self.canvas.create_window(0, 
                                      get_y_val(), 
                                      anchor=tkinter.NW,
                                      window=Label(self.canvas, text=goal.name, font=self.GOAL_ENTRY_FONT))
            DELETE_BUTTON_OFFSET = 40
            CHECK_BOX_RIGHT_OFFSET = DELETE_BUTTON_OFFSET + self.GOAL_ENTRY_HEIGHT
            goal._tkinter_state = tkinter.BooleanVar(value=goal.state)
            self.canvas.create_window(self.GOALS_CANVAS_WIDTH-CHECK_BOX_RIGHT_OFFSET-y_padding,
                                      get_y_val(),
                                      anchor=tkinter.NW,
                                      window=Checkbutton(self.canvas,
                                                         variable=goal._tkinter_state,
                                                         onvalue=True,
                                                         offvalue=False,
                                                         command=lambda goal_id=goal.id: self.client.toggle_goal_state(goal_id)))
            self.canvas.create_window(self.GOALS_CANVAS_WIDTH-DELETE_BUTTON_OFFSET-y_padding,
                                      get_y_val(),
                                      anchor=tkinter.NW,
                                      window=Button(self.canvas,
                                                    text='delete',
                                                    command=lambda goal_id=goal.id: self.delete_goal(goal_id)))

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