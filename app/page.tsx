import { PaperLibrary } from "./paper-library";
import { LibraryMetadataError } from "@/lib/library-state";
import { scanPaperLibrary } from "@/lib/paper-library";

export const dynamic = "force-dynamic";

export default async function Home() {
  try {
    return <PaperLibrary initialPapers={await scanPaperLibrary()} />;
  } catch (error) {
    if (error instanceof LibraryMetadataError) {
      return <PaperLibrary initialPapers={[]} initialLibraryMetadataError />;
    }
    throw error;
  }
}
