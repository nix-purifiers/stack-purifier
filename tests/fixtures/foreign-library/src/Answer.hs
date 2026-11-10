{-# LANGUAGE ForeignFunctionInterface #-}
module Answer where
import LocalAnswer (nativeAnswer)
import Foreign.C.Types
foreign export ccall "purifier_answer" answer :: CInt -> IO CInt
answer :: CInt -> IO CInt
answer = nativeAnswer
