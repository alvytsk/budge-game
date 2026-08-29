import { type ChangeEvent, useEffect, useState } from "react";
import {
  useAddImage,
  useCategory,
  useEditCategory,
  useEditImage,
  useSetImageActive,
  useUploadMedia,
} from "@/features/library";
import { mediaUrl } from "@/shared/config";

export function CategoryEditor({ categoryId }: { categoryId: string }) {
  const detail = useCategory(categoryId);
  const edit = useEditCategory();
  const addImage = useAddImage();
  const editImage = useEditImage();
  const setImageActive = useSetImageActive();
  const upload = useUploadMedia();

  const [title, setTitle] = useState("");
  const [isSecret, setIsSecret] = useState(false);

  // The form mirrors the server's value until the operator edits it.
  // Query's structural sharing keeps `detail.data` referentially stable
  // while §5.3's `version` and the rest of the row are unchanged, so a
  // background refetch of the same category never clobbers a half-typed
  // title — only a genuinely different value re-seeds the form.
  useEffect(() => {
    if (detail.data) {
      setTitle(detail.data.category.title);
      setIsSecret(detail.data.category.is_secret);
    }
  }, [detail.data]);

  async function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const stored = await upload(file);
    await addImage.mutateAsync({ categoryId, media_sha256: stored.media_sha256, answer_text: "" });
    event.target.value = "";
  }

  if (!detail.data) return <p className="p-6 text-stage-muted">…</p>;

  return (
    <section className="flex flex-col gap-6 p-6">
      <div className="flex items-end gap-4">
        <label className="flex flex-col gap-1 text-sm text-stage-muted">
          Название
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            className="rounded-lg bg-white/10 px-3 py-2 text-lg text-stage-ink"
          />
        </label>
        <label className="flex items-center gap-2 pb-3 text-sm text-stage-muted">
          <input
            type="checkbox"
            checked={isSecret}
            onChange={(event) => setIsSecret(event.target.checked)}
          />
          Секретная
        </label>
        <button
          type="button"
          onClick={() => void edit.mutateAsync({ categoryId, title, is_secret: isSecret })}
          className="rounded-lg bg-white/15 px-4 py-2"
        >
          Сохранить
        </button>
      </div>

      <label className="flex w-fit cursor-pointer flex-col gap-1 text-sm text-stage-muted">
        Добавить картинку
        <input type="file" accept="image/*" onChange={onFile} className="text-stage-ink" />
      </label>

      <ul className="grid grid-cols-4 gap-4">
        {detail.data.images.map((image) => (
          <li
            key={image.id}
            data-testid={`image-${image.id}`}
            data-active={image.is_active}
            className="flex flex-col gap-2 rounded-xl bg-white/5 p-3 data-[active=false]:opacity-40"
          >
            <img
              src={mediaUrl(image.media_sha256)}
              alt={image.answer_text}
              className="h-32 w-full rounded-lg object-cover"
            />
            <input
              defaultValue={image.answer_text}
              aria-label={`Ответ ${image.id}`}
              onBlur={(event) => {
                // Saved on blur rather than per keystroke: the answer is
                // typed once, and §5.3 makes every edit bump the
                // category's version — one write per field, not one per
                // character.
                if (event.target.value === image.answer_text) return;
                void editImage.mutateAsync({
                  imageId: image.id,
                  media_sha256: image.media_sha256,
                  answer_text: event.target.value,
                });
              }}
              className="rounded bg-white/10 px-2 py-1 text-sm"
            />
            <button
              type="button"
              onClick={() =>
                void setImageActive.mutateAsync({ imageId: image.id, is_active: !image.is_active })
              }
              className="text-xs text-stage-muted underline"
            >
              {image.is_active ? "Выключить" : "Включить"}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
