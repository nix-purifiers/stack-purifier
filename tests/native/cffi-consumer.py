from cffi import FFI
import json, pathlib, sys
ffi = FFI()
ffi.cdef('''int purifier_init(void); void purifier_term(void); int purifier_answer(int);
typedef struct { const char *name; void *base; const char *symbol; void *address; } Info;
int dladdr(void *, Info *);''')
p = pathlib.Path(sys.argv[1]).resolve(); lib = ffi.dlopen(str(p)); host = ffi.dlopen(None)
assert lib.purifier_init() == 1
assert [lib.purifier_answer(0),lib.purifier_answer(1)] == [42,43]
info = ffi.new('Info *'); assert host.dladdr(ffi.cast('void *', lib.purifier_answer), info)
assert pathlib.Path(ffi.string(info.name).decode()).resolve() == p
lib.purifier_term(); ffi.dlclose(lib)
print(json.dumps({'values':[42,43], 'origin':str(p), 'lifecycle':'one init/term/close; natural exit'}))
