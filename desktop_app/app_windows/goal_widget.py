import tkinter

from tkinter import Frame, Label, Button, Checkbutton
from tkinter.scrolledtext import ScrolledText
from typing import *
from datetime import datetime, timedelta

from playsound import playsound
from desktop_app.client import ClientV2
from desktop_app.goal import Goal
from .goals_window import GoalsWindow


class GoalWidget(Frame):
    WIDTH = 500
    GOALS_CANVAS_WIDTH = int(WIDTH * 4/5)
    GOAL_ENTRY_HEIGHT = 25
    GOAL_ENTRY_FONT = 'Helvetica 9'

    def __init__(self, 
                 parent_widget: Any, 
                 goals_window: GoalsWindow, 
                 goal: Goal, 
                 client: ClientV2, 
                 x_val: int, 
                 y_val: int,
                 disable_dragging: bool = False):
        super().__init__(parent_widget)
        self.client = client
        self.goals_window = goals_window
        self.x_val = x_val
        self.y_val = y_val
        self.goal = goal
        self.disable_dragging = disable_dragging
        self._drag_start = datetime.now()

        self.construct_row()

    def construct_row(self):
        x_padding = 2
        y_padding = 0

        label_frame = Frame(self, width=self.GOALS_CANVAS_WIDTH-80, height=self.GOAL_ENTRY_HEIGHT)
        label_frame.pack(side=tkinter.LEFT)
        label_frame.pack_propagate(False) # Stops child widgets of label_frame from resizing it
        
        def toggle_collapse():
            if not self.goal.children:
                return
            self.goal.collapsed = not self.goal.collapsed
            self.goals_window.refresh_goals_canvas()
        
        collapse_button = Button(label_frame, 
                                 text='>' if self.goal.children else '', 
                                 command=toggle_collapse,
                                 width=1)
        collapse_button.pack(side=tkinter.LEFT, padx=(x_padding+self.x_val, 0))

        label_window = Label(label_frame, text=self.goal.name, font=self.GOAL_ENTRY_FONT, anchor='w', name=str(self.goal.id))
        label_window.pack(side=tkinter.LEFT)

        if self.disable_dragging:
            # double click doesn't work due to single click being mapped already
            label_window.bind('<Double-Button-1>', lambda _, goal_id=self.goal.id: self.spawn_goal_details_window(goal_id))
        else:
            label_window.bind('<Button-1>', self.on_lclick)
            label_window.bind('<B1-Motion>', self.on_drag_motion)
            label_window.bind('<ButtonRelease-1>', self.on_drag_end)
        DELETE_BUTTON_OFFSET = 40
        CHECK_BOX_RIGHT_OFFSET = DELETE_BUTTON_OFFSET + self.GOAL_ENTRY_HEIGHT
        self._tkinter_state = tkinter.BooleanVar(value=self.goal.state)
        def toggle_goal_state(goal_id: int):
            if self._tkinter_state.get():
                playsound('data/fx1.wav', block=False)
            self.client.toggle_goal_state(goal_id)
            self.goals_window.refresh_all_goal_windows()
        delete_button = Button(self,
                               text='delete',
                               command=lambda goal_id=self.goal.id: self.goals_window.delete_goal(goal_id))
        delete_button.pack(side=tkinter.RIGHT)
        check_button = Checkbutton(self,
                                   variable=self._tkinter_state,
                                   onvalue=True,
                                   offvalue=False,
                                   command=lambda goal_id=self.goal.id: toggle_goal_state(goal_id))
        check_button.pack(side=tkinter.RIGHT)
    
    def is_double_click(self) -> bool:
        return datetime.now() - self._drag_start < timedelta(milliseconds=200)

    def on_lclick(self, event: tkinter.Event):
        if self.is_double_click():
            self.spawn_goal_details_window(self.goal.id)
        else:
            self.lift()
            self._drag_start_x, self._drag_start_y = event.x, event.y
        self._drag_start = datetime.now()
    
    def on_drag_motion(self, event: tkinter.Event):
        if self.is_double_click():
            return
        x = self.winfo_x() - self._drag_start_x + event.x
        y = self.winfo_y() - self._drag_start_y + event.y
        self.place(x=x, y=y)

    def on_drag_end(self, event: tkinter.Event):
        if self.is_double_click():
            return
        self.place_forget()
        goal_id = self.master.winfo_containing(*self.master.winfo_pointerxy()).winfo_name()
        # check if goal_id is actually an int
        try:
            goal_id = int(goal_id)
        except ValueError:
            self.goals_window.refresh_goals_canvas()
            return
        
        self.goals_window.setup_parent(self.goal.id, goal_id)

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
            self.goals_window.refresh_all_goal_windows()
            close_window()
        Button(top, 
               text="Remove from Daily" if is_daily else "Make Daily", 
               command=toggle_daily).pack(side=tkinter.LEFT)
        def unparent():
            self.client.unparent_goal(goal_id)
            self.goals_window.refresh_all_goal_windows()
            close_window()
        Button(top, text="Unparent", command=unparent).pack(side=tkinter.LEFT)
        