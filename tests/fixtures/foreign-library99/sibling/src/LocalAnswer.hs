module LocalAnswer where
import Control.Exception (evaluate)
import qualified Data.ByteString as BS
import Data.List.Split (chunksOf)
import Foreign.C.Types
import System.Mem (performGC)
localAnswer :: Int
localAnswer = 99
nativeAnswer :: CInt -> IO CInt
nativeAnswer delta = do
  let bytes = BS.pack (concat (chunksOf 7 [0 .. 41]))
  count <- evaluate (BS.length bytes)
  performGC
  pure (fromIntegral count + delta + fromIntegral localAnswer - 42)
