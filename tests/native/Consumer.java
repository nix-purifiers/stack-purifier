import java.lang.foreign.*;
import java.lang.invoke.*;
import java.nio.file.*;
public class Consumer {
  public static void main(String[] args) throws Throwable {
    Path expected = Path.of(args[0]).toRealPath(); Linker linker = Linker.nativeLinker();
    try (Arena a = Arena.ofConfined()) {
      SymbolLookup lib = SymbolLookup.libraryLookup(expected, a);
      MethodHandle init = linker.downcallHandle(lib.find("purifier_init").orElseThrow(), FunctionDescriptor.of(ValueLayout.JAVA_INT));
      MethodHandle term = linker.downcallHandle(lib.find("purifier_term").orElseThrow(), FunctionDescriptor.ofVoid());
      MemorySegment symbol = lib.find("purifier_answer").orElseThrow();
      MethodHandle call = linker.downcallHandle(symbol, FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_INT));
      if ((int)init.invokeExact()!=1 || (int)call.invokeExact(0)!=42 || (int)call.invokeExact(1)!=43) throw new AssertionError("values");
      MemorySegment info = a.allocate(4 * ValueLayout.ADDRESS.byteSize(), ValueLayout.ADDRESS.byteAlignment());
      MethodHandle origin = linker.downcallHandle(linker.defaultLookup().find("dladdr").orElseThrow(), FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.ADDRESS, ValueLayout.ADDRESS));
      if ((int)origin.invokeExact(symbol, info)==0) throw new AssertionError("dladdr");
      if (!Path.of(info.get(ValueLayout.ADDRESS,0).reinterpret(4096).getString(0)).toRealPath().equals(expected)) throw new AssertionError("origin");
      term.invokeExact();
    }
    System.out.println("Java42/43 + origin + one RTS init/term + confined close + natural return passed");
  }
}
