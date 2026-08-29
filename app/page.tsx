import { PaperLibrary } from "./paper-library";
import { scanPaperLibrary } from "@/lib/paper-library";

export const dynamic = "force-dynamic";

export default async function Home() {
  return <PaperLibrary initialPapers={await scanPaperLibrary()} />;
}
