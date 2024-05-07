import time
import tkinter

from tkinter import Button
from tkcalendar import Calendar
from datetime import datetime

from desktop_app.goal import Goal


def select_date(frame: tkinter.Frame, current_date: datetime = datetime.now()) -> datetime:
    top = tkinter.Toplevel(frame)
    cal = Calendar(
        top, 
        font="Arial 14", 
        selectmode='day', 
        locale='en_AU',
        cursor="hand1",
        year=current_date.year,
        month=current_date.month,
        day=current_date.day)
    cal.pack(fill="both", expand=True)

    selected_date = Goal.NULL_DATE
    def set_date():
        nonlocal selected_date
        date = cal.selection_get()
        selected_date = datetime(date.year, date.month, date.day, hour=9)
    Button(top, text="OK", command=set_date).pack()
    top.bind('<Escape>', lambda _: top.destroy())
    width = 300
    height = 300
    top.geometry(f"{width}x{height}+{frame.master.winfo_rootx()}+{frame.master.winfo_rooty()}")

    while selected_date == Goal.NULL_DATE:
        if not top.winfo_exists():
            break
        top.update()
        time.sleep(0.1)

    top.destroy()

    return selected_date
