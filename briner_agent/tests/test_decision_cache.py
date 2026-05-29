import time
import unittest

from classifiers.decision_cache import DecisionCache, _stem_pattern


class DecisionCacheTests(unittest.TestCase):
    def test_stem_pattern_normalizes_digits(self):
        self.assertEqual(_stem_pattern("invoice_001.pdf"), _stem_pattern("invoice_099.pdf"))

    def test_stem_pattern_is_case_insensitive(self):
        self.assertEqual(_stem_pattern("InVoIce.PDF"), _stem_pattern("invoice.PDF"))

    def test_cache_miss_on_empty(self):
        cache = DecisionCache(max_size=10, ttl_seconds=60)
        self.assertIsNone(cache.get("unknown.pdf", ".pdf"))

    def test_put_and_get(self):
        cache = DecisionCache(max_size=10, ttl_seconds=60)
        cache.put("actividad.pdf", ".pdf", "Universidad y Estudio/Actividades y Tareas")
        result = cache.get("actividad.pdf", ".pdf")
        self.assertEqual(result, "Universidad y Estudio/Actividades y Tareas")

    def test_ttl_expiry(self):
        cache = DecisionCache(max_size=10, ttl_seconds=0.05)
        cache.put("a.pdf", ".pdf", "Varios")
        time.sleep(0.1)
        self.assertIsNone(cache.get("a.pdf", ".pdf"))

    def test_lru_eviction(self):
        cache = DecisionCache(max_size=2, ttl_seconds=60)
        cache.put("a.txt", ".txt", "Cat1")
        cache.put("b.txt", ".txt", "Cat2")
        cache.put("c.txt", ".txt", "Cat3")  # evicts "a.txt"
        self.assertIsNone(cache.get("a.txt", ".txt"))
        self.assertIsNotNone(cache.get("b.txt", ".txt"))

    def test_same_pattern_shares_cache_entry(self):
        cache = DecisionCache(max_size=10, ttl_seconds=60)
        cache.put("invoice_001.pdf", ".pdf", "Documentos Personales/Finanzas y Salud")
        result = cache.get("invoice_042.pdf", ".pdf")
        self.assertEqual(result, "Documentos Personales/Finanzas y Salud")

    def test_hit_rate_tracking(self):
        cache = DecisionCache(max_size=10, ttl_seconds=60)
        cache.put("a.pdf", ".pdf", "Varios")
        cache.get("a.pdf", ".pdf")   # hit
        cache.get("b.pdf", ".pdf")   # miss
        stats = cache.stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hit_rate"], 0.5)

    def test_clear_resets_stats(self):
        cache = DecisionCache(max_size=10, ttl_seconds=60)
        cache.put("a.pdf", ".pdf", "Varios")
        cache.get("a.pdf", ".pdf")
        cache.clear()
        stats = cache.stats()
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(stats["size"], 0)

    def test_put_updates_existing_entry(self):
        cache = DecisionCache(max_size=10, ttl_seconds=60)
        cache.put("a.pdf", ".pdf", "Varios")
        cache.put("a.pdf", ".pdf", "Multimedia/Imagenes y Capturas")
        self.assertEqual(cache.get("a.pdf", ".pdf"), "Multimedia/Imagenes y Capturas")


if __name__ == "__main__":
    unittest.main()
