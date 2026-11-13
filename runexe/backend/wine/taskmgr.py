from runexe.backend.logger import Logger
from runexe.backend.wine.wineprogram import WineProgram

logging = Logger()


class Taskmgr(WineProgram):
    program = "Wine Task Manager"
    command = "taskmgr"
