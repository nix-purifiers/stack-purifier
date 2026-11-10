#include "answer.h"
#include <stddef.h>
int purifier_init(void) {
  int argc = 1;
  static char name[] = "purifier-foreign-proof";
  static char *argv[] = {name, NULL};
  char **pargv = argv;
  hs_init(&argc, &pargv);
  return 1;
}
void purifier_term(void) { hs_exit(); }
