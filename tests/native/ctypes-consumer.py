import ctypes, json, pathlib, sys
p = pathlib.Path(sys.argv[1]).resolve()
from purifier_native import load
d = load("answer")
d.purifier_answer.argtypes = [ctypes.c_int]
d.purifier_answer.restype = ctypes.c_int
d.purifier_term.restype = None
assert d.purifier_init() == 1
assert [d.purifier_answer(0), d.purifier_answer(1)] == [42, 43]
class Info(ctypes.Structure):
    _fields_ = [('fname', ctypes.c_char_p), ('base', ctypes.c_void_p),
                ('sname', ctypes.c_char_p), ('saddr', ctypes.c_void_p)]
i = Info(); host = ctypes.CDLL(None)
assert host.dladdr(ctypes.cast(d.purifier_answer, ctypes.c_void_p), ctypes.byref(i))
assert pathlib.Path(i.fname.decode()).resolve() == p
d.purifier_term()
print(json.dumps({'values':[42,43], 'origin':str(p), 'lifecycle':'one init/term; CDLL retained until process exit'}))
