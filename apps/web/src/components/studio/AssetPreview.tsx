import { useEffect, useState } from "react";

import type { Asset } from "../../types/stageFive";

export function AssetPreview({ asset }: { asset: Asset }) {
  const [isLightboxOpen, setIsLightboxOpen] = useState(false);
  const canPreviewMedia = asset.uri.startsWith("http") || asset.uri.startsWith("data:");
  const canPreviewImage = asset.asset_type === "image" && canPreviewMedia;
  const canPreviewVideo =
    (asset.asset_type === "video" || asset.asset_type === "final_video") && asset.uri.startsWith("http");
  const label =
    asset.asset_type === "image"
      ? "IMAGE"
      : asset.asset_type === "video" || asset.asset_type === "final_video"
        ? "VIDEO"
        : "FILE";

  useEffect(() => {
    if (!isLightboxOpen) {
      return undefined;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsLightboxOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isLightboxOpen]);

  return (
    <div className={`asset-preview ${asset.asset_type}`}>
      {canPreviewImage ? (
        <>
          <button
            aria-label="查看大图"
            className="asset-preview-image-button"
            onClick={() => setIsLightboxOpen(true)}
            title="查看大图"
            type="button"
          >
            <img src={asset.uri} alt={asset.entity_type ?? asset.asset_type} />
          </button>
          {isLightboxOpen ? <AssetImageLightbox asset={asset} onClose={() => setIsLightboxOpen(false)} /> : null}
        </>
      ) : null}
      {canPreviewVideo ? (
        <video controls preload="metadata" src={asset.uri}>
          <track kind="captions" />
        </video>
      ) : null}
      {!canPreviewImage && !canPreviewVideo ? (
        <span>
          {label}
          <small>{previewFallbackMessage(asset)}</small>
        </span>
      ) : null}
    </div>
  );
}

function AssetImageLightbox({ asset, onClose }: { asset: Asset; onClose: () => void }) {
  return (
    <div className="asset-lightbox" onClick={onClose} role="presentation">
      <section
        aria-label="图片预览"
        aria-modal="true"
        className="asset-lightbox-panel"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className="asset-lightbox-head">
          <div>
            <strong>{asset.asset_role ?? asset.entity_type ?? "image"} · v{asset.version}</strong>
            <span>{asset.provider ?? "provider"} · {asset.model ?? "model"}</span>
          </div>
          <button aria-label="关闭" className="asset-lightbox-close" onClick={onClose} type="button">
            ×
          </button>
        </div>
        <div className="asset-lightbox-canvas">
          <img src={asset.uri} alt={asset.entity_type ?? asset.asset_type} />
        </div>
        {asset.prompt ? <p>{asset.prompt}</p> : null}
      </section>
    </div>
  );
}

function previewFallbackMessage(asset: Asset) {
  if (asset.asset_type === "image" && asset.uri.startsWith("/")) {
    return "服务返回内部路径，浏览器无法预览";
  }
  if (asset.uri.startsWith("mock://")) {
    return "模拟资产无真实文件";
  }
  return "暂无可预览地址";
}
