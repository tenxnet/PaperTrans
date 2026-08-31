export const SAFE_ARXIV_RENDERER_GENERATION = "3";

type ArtifactPublicationState = {
  status?: string;
  sourceType: string;
  provider?: string;
  rendererVersion?: string;
};

export function isArtifactManifestPublishable(
  manifest: ArtifactPublicationState,
): boolean {
  if (!["completed", "needs_review"].includes(manifest.status ?? "")) return false;
  if (
    manifest.sourceType === "pdf"
    && manifest.provider === "none"
    && manifest.status === "needs_review"
  ) return false;
  if (manifest.sourceType !== "arxiv") return true;
  return new RegExp(
    `^${SAFE_ARXIV_RENDERER_GENERATION}-[0-9a-f]{12}$`,
  ).test(manifest.rendererVersion ?? "");
}
