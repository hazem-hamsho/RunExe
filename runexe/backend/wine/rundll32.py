from runexe.backend.logger import Logger
from runexe.backend.wine.wineprogram import WineProgram

logging = Logger()


class RunDLL32(WineProgram):
    program = "32-bit DLLs loader and runner"
    command = "rundll32"
