import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback } from "react";
import type {
  CategoryDetailBody,
  CategorySummaryBody,
  ImageBody,
  ReadinessBody,
  UploadedMediaBody,
} from "@/shared/api";

const LIBRARY = "library";

async function json<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) throw new Error(`${input}: ${response.status}`);
  return (await response.json()) as T;
}

function payload(value: unknown): RequestInit {
  return { headers: { "content-type": "application/json" }, body: JSON.stringify(value) };
}

export function useCategories(): UseQueryResult<CategorySummaryBody[]> {
  return useQuery({
    queryKey: [LIBRARY, "categories"],
    queryFn: () => json<CategorySummaryBody[]>("/api/library/categories"),
  });
}

export function useCategory(categoryId: string): UseQueryResult<CategoryDetailBody> {
  return useQuery({
    queryKey: [LIBRARY, "category", categoryId],
    queryFn: () => json<CategoryDetailBody>(`/api/library/categories/${categoryId}`),
  });
}

export function useReadiness(cells: number): UseQueryResult<ReadinessBody> {
  return useQuery({
    queryKey: [LIBRARY, "readiness", cells],
    queryFn: () => json<ReadinessBody>(`/api/library/readiness?cells=${cells}`),
  });
}

/** Every mutation invalidates the whole `library` key rather than naming
 * what it touched. The library is small, one operator edits it, and a
 * missed invalidation is a screen that lies. */
function useLibraryMutation<TArgs, TResult>(
  run: (args: TArgs) => Promise<TResult>,
): UseMutationResult<TResult, Error, TArgs> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [LIBRARY] });
    },
  });
}

export function useCreateCategory() {
  return useLibraryMutation(({ title, is_secret }: { title: string; is_secret: boolean }) =>
    json<CategorySummaryBody>("/api/library/categories", {
      method: "POST",
      ...payload({ title, is_secret }),
    }),
  );
}

export function useEditCategory() {
  return useLibraryMutation(
    ({ categoryId, title, is_secret }: { categoryId: string; title: string; is_secret: boolean }) =>
      json<CategoryDetailBody>(`/api/library/categories/${categoryId}`, {
        method: "PUT",
        ...payload({ title, is_secret }),
      }),
  );
}

export function useSetCategoryActive() {
  return useLibraryMutation(
    ({ categoryId, is_active }: { categoryId: string; is_active: boolean }) =>
      json<CategoryDetailBody>(`/api/library/categories/${categoryId}/active`, {
        method: "PUT",
        ...payload({ is_active }),
      }),
  );
}

export function useAddImage() {
  return useLibraryMutation(
    (args: { categoryId: string; media_sha256: string; answer_text: string }) =>
      json<ImageBody>(`/api/library/categories/${args.categoryId}/images`, {
        method: "POST",
        ...payload({ media_sha256: args.media_sha256, answer_text: args.answer_text }),
      }),
  );
}

export function useEditImage() {
  return useLibraryMutation(
    (args: { imageId: string; media_sha256: string; answer_text: string }) =>
      json<ImageBody>(`/api/library/images/${args.imageId}`, {
        method: "PUT",
        ...payload({ media_sha256: args.media_sha256, answer_text: args.answer_text }),
      }),
  );
}

export function useSetImageActive() {
  return useLibraryMutation(({ imageId, is_active }: { imageId: string; is_active: boolean }) =>
    json<ImageBody>(`/api/library/images/${imageId}/active`, {
      method: "PUT",
      ...payload({ is_active }),
    }),
  );
}

export function useReorderImages() {
  return useLibraryMutation(
    ({ categoryId, image_ids }: { categoryId: string; image_ids: string[] }) =>
      json<CategoryDetailBody>(`/api/library/categories/${categoryId}/images/order`, {
        method: "PUT",
        ...payload({ image_ids }),
      }),
  );
}

/** H9: store the bytes, get the digest back. The image row is a second
 * call, and the server refuses a digest it has not stored. */
export function useUploadMedia(): (file: File) => Promise<UploadedMediaBody> {
  return useCallback(async (file: File) => {
    const response = await fetch("/api/media", {
      method: "POST",
      headers: { "content-type": file.type },
      body: file,
    });
    if (!response.ok) throw new Error(`upload: ${response.status}`);
    return (await response.json()) as UploadedMediaBody;
  }, []);
}
