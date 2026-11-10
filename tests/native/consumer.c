#define _GNU_SOURCE
#include "answer.h"
#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv) {
  assert(argc == 2);
  void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!library) { puts(dlerror()); return 1; }
  int (*initialize)(void) = dlsym(library, "purifier_init");
  void (*terminate)(void) = dlsym(library, "purifier_term");
  __typeof__(purifier_answer) *answer = dlsym(library, "purifier_answer");
  assert(initialize && terminate && answer && initialize() == 1);
  assert(answer(0) == 42 && answer(1) == 43);
  Dl_info info; assert(dladdr(answer, &info));
  char *actual = realpath(info.dli_fname, NULL), *expected = realpath(argv[1], NULL);
  assert(actual && expected && strcmp(actual, expected) == 0);
  free(actual); free(expected);
  terminate(); assert(dlclose(library) == 0);
  puts("generated-header C ABI42/43 + origin + explicit RTS lifecycle + scoped close passed");
}
