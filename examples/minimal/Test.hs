import Data.List.Split (splitOn)

main :: IO ()
main = if splitOn "," "locked,Stack" == ["locked", "Stack"]
  then pure ()
  else fail "locked split dependency returned the wrong result"
