STATE = {"n": 0}

def get():
    return STATE["n"]

def set(n):
    STATE["n"] = n

def inc():
    STATE["n"] = STATE["n"] + 1
