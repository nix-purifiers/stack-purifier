-- Read Stack's actual native Cabal configuration, not a second build plan.
import Distribution.Simple.Configure (getPersistBuildConfig)
import qualified Distribution.Types.LocalBuildInfo as L
import qualified Distribution.PackageDescription as P
import qualified Distribution.Simple.LocalBuildInfo as LI
import Distribution.Simple.BuildPaths (flibBuildDir)
import Distribution.Simple.Compiler (compilerId)
import Distribution.Simple.InstallDirs (CopyDest(..),flibdir,includedir)
import Distribution.Types.ComponentName (ComponentName(..))
import qualified Distribution.Types.ComponentLocalBuildInfo as C
import qualified Distribution.Simple.PackageIndex as Index
import qualified Distribution.InstalledPackageInfo as I
import Distribution.Pretty (prettyShow)
import qualified Data.Map as M
import Data.Char (ord)
import Data.List (intercalate)
import Numeric (showHex)
import System.Environment (getArgs)
import System.FilePath (takeDirectory,(</>))

js :: String -> String
js s = "\"" ++ concatMap escape s ++ "\"" where
 escape '"' = "\\\""
 escape '\\' = "\\\\"
 escape '\n' = "\\n"
 escape '\r' = "\\r"
 escape '\t' = "\\t"
 escape c | ord c < 32 = "\\u" ++ replicate (4-length hex) '0' ++ hex where hex=showHex (ord c) ""
 escape c = [c]
obj fields = "{" ++ intercalate "," [js k ++ ":" ++ v | (k,v)<-fields] ++ "}"
arr xs = "[" ++ intercalate "," xs ++ "]"
strings = arr . map js
info p = obj [("unit",js $ prettyShow $ I.installedUnitId p),("package",js $ prettyShow $ I.sourcePackageId p),("depends",strings $ map prettyShow $ I.depends p),("libraryDirs",strings $ I.libraryDirs p),("dynamicLibraryDirs",strings $ I.libraryDynDirs p),("includeDirs",strings $ I.includeDirs p),("hsLibraries",strings $ I.hsLibraries p)]
closure lbi seen [] = seen
closure lbi seen (uid:rest)
 | M.member (prettyShow uid) seen = closure lbi seen rest
 | otherwise = case Index.lookupUnitId (L.installedPkgs lbi) uid of
    Nothing -> error $ "Native Stack/Cabal dependency missing: " ++ prettyShow uid
    Just p -> closure lbi (M.insert (prettyShow uid) p seen) (I.depends p ++ rest)
main = do
 [dist,destination,output] <- getArgs
 lbi <- getPersistBuildConfig dist
 let pd = L.localPkgDescr lbi
 let report f = case M.findWithDefault [] (CFLibName (P.foreignLibName f)) (L.componentNameMap lbi) of
      [c] -> let dirs=LI.absoluteComponentInstallDirs pd lbi (C.componentUnitId c) (if destination=="-" then NoCopyDest else CopyTo destination)
                 deps=map fst (C.componentPackageDeps c)
                 graph=closure lbi M.empty deps
             in obj [("selector",js $ prettyShow (P.pkgName $ P.package pd) ++ ":flib:" ++ prettyShow (P.foreignLibName f)),("package",js $ prettyShow $ P.package pd),("component",js $ prettyShow $ P.foreignLibName f),("type",js $ show $ P.foreignLibType f),("unit",js $ prettyShow $ C.componentUnitId c),("nativeBuildDirectory",js $ takeDirectory $ flibBuildDir lbi f),("nativeLibraryDirectory",js $ flibdir dirs),("nativeIncludeDirectory",js $ includedir dirs),("installIncludes",strings $ P.installIncludes $ P.foreignLibBuildInfo f),("versionInfo",js $ show $ P.foreignLibVersionInfo f),("versionLinux",js $ show $ P.foreignLibVersionLinux f),("dependencies",strings $ map prettyShow deps),("units",arr $ map info $ M.elems graph)]
      _ -> error "Missing or ambiguous native foreign-library component configuration"
 writeFile output $ obj [("buildType",js $ show $ P.buildType pd),("compiler",js $ prettyShow $ compilerId $ L.compiler lbi),("platform",js $ prettyShow $ L.hostPlatform lbi),("package",js $ prettyShow $ P.package pd),("foreignLibraries",arr $ map report $ P.foreignLibs pd)] ++ "\n"
