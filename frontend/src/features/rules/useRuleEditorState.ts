/**
 * Stage 4 — Rule editor body state hook.
 *
 * Owns the form state for the rule editor: name, description,
 * priority, enabled flag, definition (both as object and as text),
 * the active tab, and the error/pending status of the save mutation.
 * Also exposes the helper that round-trips edits between the Visual
 * builder and the JSON textarea so neither view goes stale.
 *
 * Returns a single object with state + setters + handlers. The
 * orchestrator (``RuleEditorBody``) calls them in JSX; no hidden
 * coupling.
 *
 * Behavior is preserved exactly — the same useState declarations,
 * the same parsedDefinition memo, the same Escape-to-back handler,
 * the same submit logic — so the existing 10 editor tests continue
 * to pass without modification.
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type RefObject,
} from "react";

import {
  useCreateRule,
  useDuplicateRule,
  useRuleVocabulary,
  useUpdateRule,
  type Rule,
  type RuleDefinition,
} from "@/hooks/useRules";
import { toast } from "@/lib/toast";

import { DEFAULT_DEFINITION, type EditorTab } from "./editorShared";

export interface UseRuleEditorState {
  /* Form fields. */
  name: string;
  setName: (s: string) => void;
  description: string;
  setDescription: (s: string) => void;
  priority: number;
  setPriority: (n: number) => void;
  enabled: boolean;
  setEnabled: (b: boolean) => void;

  /* Definition — kept as both object (for the Visual builder) and
   * text (for the JSON tab). ``commitFromVisual`` and
   * ``commitFromJson`` keep them in sync. */
  definition: RuleDefinition;
  definitionText: string;
  parsedDefinition: { ok: boolean; value?: RuleDefinition; error?: string };
  commitFromVisual: (next: RuleDefinition) => void;
  commitFromJson: (text: string) => void;

  /* Tab + readOnly flags. */
  tab: EditorTab;
  setTab: (t: EditorTab) => void;
  isBuiltin: boolean;
  readOnly: boolean;

  /* Hooks the body needs to read inline (vocabulary for the Visual
   * tab, duplicate for the read-only CTA). Surfaced here so the
   * body component is purely declarative. */
  vocabulary: ReturnType<typeof useRuleVocabulary>;
  duplicateMutation: ReturnType<typeof useDuplicateRule>;

  /* Save status + handlers. */
  isPending: boolean;
  error: string | null;
  formRef: RefObject<HTMLFormElement>;
  onSubmit: (e: FormEvent) => Promise<void>;
  onDuplicate: () => Promise<void>;
  title: string;
}

export function useRuleEditorState({
  rule,
  onDone,
}: {
  rule: Rule | null;
  onDone: () => void;
}): UseRuleEditorState {
  const create = useCreateRule();
  const update = useUpdateRule();
  const duplicateMutation = useDuplicateRule();
  const vocabulary = useRuleVocabulary();
  const isBuiltin = !!rule?.is_builtin;
  // Read-only flag derived from the rule's origin. Builtins flip
  // every input to disabled + hide the Save button. The page remains
  // useful: operators can inspect the definition, run dry-run, and
  // one-click duplicate to a custom variant.
  const readOnly = isBuiltin;

  const [name, setName] = useState(rule?.name ?? "");
  const [description, setDescription] = useState(rule?.description ?? "");
  const [priority, setPriority] = useState(rule?.priority ?? 100);
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);
  const [definition, setDefinition] = useState<RuleDefinition>(
    rule?.definition ?? DEFAULT_DEFINITION,
  );
  const [definitionText, setDefinitionText] = useState(() =>
    JSON.stringify(rule?.definition ?? DEFAULT_DEFINITION, null, 2),
  );
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<EditorTab>("visual");
  // Stage 30: the Save button lives in the PageHeader actions
  // (outside the form's DOM subtree), so a ref to the form gives
  // the header button a direct handle to .requestSubmit() it.
  const formRef = useRef<HTMLFormElement | null>(null);

  // Esc returns to the list — same affordance the modal had,
  // useful here too for the operator who wants out fast.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDone();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onDone]);

  const parsedDefinition = useMemo<{
    ok: boolean;
    value?: RuleDefinition;
    error?: string;
  }>(() => {
    try {
      const value = JSON.parse(definitionText) as RuleDefinition;
      return { ok: true, value };
    } catch (err) {
      return { ok: false, error: (err as Error).message };
    }
  }, [definitionText]);

  function commitFromVisual(next: RuleDefinition) {
    setDefinition(next);
    setDefinitionText(JSON.stringify(next, null, 2));
  }

  function commitFromJson(text: string) {
    setDefinitionText(text);
    try {
      const parsed = JSON.parse(text) as RuleDefinition;
      setDefinition(parsed);
    } catch {
      // Leave ``definition`` at its last good state; Visual tab
      // will show that until the JSON parses again.
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!parsedDefinition.ok) {
      setError(`Invalid JSON: ${parsedDefinition.error}`);
      return;
    }
    const finalDef = parsedDefinition.value ?? definition;
    try {
      if (rule) {
        await update.mutateAsync({
          id: rule.id,
          patch: {
            name,
            description: description || undefined,
            priority,
            enabled,
            definition: finalDef,
          },
        });
        toast(`Saved ${name}`, "ok");
      } else {
        await create.mutateAsync({
          name,
          description: description || undefined,
          priority,
          enabled,
          definition: finalDef,
        });
        toast(`Created ${name}`, "ok");
      }
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function onDuplicate() {
    if (!rule) return;
    try {
      const copy = await duplicateMutation.mutateAsync(rule.id);
      toast(`Duplicated as ${copy.name}`, "ok");
      // Navigate back to the list so the operator can immediately
      // tweak the new copy by clicking its row.
      onDone();
    } catch (err) {
      toast(
        `Could not duplicate ${rule.name}: ${
          err instanceof Error ? err.message : String(err)
        }`,
        "error",
        5000,
      );
    }
  }

  const isPending = create.isPending || update.isPending;
  const title = rule ? `Edit rule · ${rule.name}` : "New rule";

  return {
    name,
    setName,
    description,
    setDescription,
    priority,
    setPriority,
    enabled,
    setEnabled,
    definition,
    definitionText,
    parsedDefinition,
    commitFromVisual,
    commitFromJson,
    tab,
    setTab,
    isBuiltin,
    readOnly,
    vocabulary,
    duplicateMutation,
    isPending,
    error,
    formRef,
    onSubmit,
    onDuplicate,
    title,
  };
}
