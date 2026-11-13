from runexe.backend.logger import Logger
from runexe.backend.wine.wineprogram import WineProgram

logging = Logger()


class Oleview(WineProgram):
    program = "OLE/COM object viewer"
    command = "oleview"
