import { useState } from "react";
import { shortfallOf, useCategories, useCreateCategory, useReadiness } from "@/features/library";
import { CategoryEditor } from "@/widgets/category-editor";

// §8's readiness is answered per board size. Twelve cells is the board the
// setup screen offers first; the operator sees a real number either way,
// and nothing here refuses anything.
const DEFAULT_CELLS = 12;

export function LibraryPage() {
  const categories = useCategories();
  const readiness = useReadiness(DEFAULT_CELLS);
  const create = useCreateCategory();
  const [picked, setPicked] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [isSecret, setIsSecret] = useState(false);

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-80 flex-col gap-4 overflow-auto border-white/10 border-r p-6">
        <h2 className="font-display text-2xl uppercase">Библиотека</h2>

        {readiness.data && (
          <p
            data-testid="readiness"
            data-ready={readiness.data.ready}
            className="rounded-lg bg-white/5 p-3 text-sm text-stage-muted data-[ready=false]:text-amber-300"
          >
            {readiness.data.ready
              ? `Тем достаточно: ${readiness.data.ordinary_available}`
              : `Не хватает: ${shortfallOf(readiness.data).join("; ")}`}
          </p>
        )}

        <ul className="flex flex-col gap-1">
          {categories.data?.map((category) => (
            <li key={category.id}>
              <button
                type="button"
                data-testid={`category-${category.id}`}
                data-secret={category.is_secret}
                onClick={() => setPicked(category.id)}
                className="flex w-full justify-between rounded px-2 py-1 text-left hover:bg-white/10 data-[secret=true]:italic"
              >
                <span className={category.is_active ? "" : "line-through opacity-50"}>
                  {category.title}
                </span>
                <span data-testid={`count-${category.id}`} className="text-stage-muted">
                  {category.active_image_count}
                </span>
              </button>
            </li>
          ))}
        </ul>

        <div className="mt-auto flex flex-col gap-2">
          <label className="flex flex-col gap-1 text-sm text-stage-muted">
            Новая тема
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              className="rounded-lg bg-white/10 px-3 py-2 text-stage-ink"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-stage-muted">
            <input
              type="checkbox"
              checked={isSecret}
              onChange={(event) => setIsSecret(event.target.checked)}
            />
            Секретная
          </label>
          <button
            type="button"
            onClick={() => {
              void create.mutateAsync({ title, is_secret: isSecret });
              setTitle("");
              setIsSecret(false);
            }}
            className="rounded-lg bg-white/15 px-4 py-2"
          >
            Создать
          </button>
        </div>
      </aside>

      <div className="min-h-0 flex-1 overflow-auto">
        {picked === null ? (
          <p className="p-6 text-stage-muted">Выберите тему слева</p>
        ) : (
          <CategoryEditor categoryId={picked} />
        )}
      </div>
    </div>
  );
}
