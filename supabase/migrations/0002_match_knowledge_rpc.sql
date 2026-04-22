-- match_knowledge: cosine-similarity search against knowledge_items.embedding
-- Used by the conversation-intelligence worker for grounded FAQ/objection handling.

CREATE OR REPLACE FUNCTION public.match_knowledge(
    p_business_id uuid,
    p_embedding vector(1536),
    p_match_count int DEFAULT 5,
    p_categories text[] DEFAULT NULL
)
RETURNS TABLE (
    id uuid,
    title text,
    body text,
    category text,
    metadata jsonb,
    similarity float
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ki.id,
        ki.title,
        ki.body,
        ki.category,
        ki.metadata,
        1 - (ki.embedding <=> p_embedding) AS similarity
    FROM knowledge_items ki
    WHERE ki.business_id = p_business_id
      AND ki.active = true
      AND ki.embedding IS NOT NULL
      AND (p_categories IS NULL OR ki.category = ANY(p_categories))
    ORDER BY ki.embedding <=> p_embedding
    LIMIT p_match_count;
END;
$$;

COMMENT ON FUNCTION public.match_knowledge IS
    'Cosine-similarity vector search over knowledge_items for a given business.';
