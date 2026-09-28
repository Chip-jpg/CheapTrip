"""Airport cluster expansion utilities (backed by the airport registry)."""
from __future__ import annotations

from typing import List, Optional, Set

from utils.airports import CLUSTER_OF, CLUSTERS


def expand_to_cluster(airport: str) -> List[str]:
    """
    Return all airports in the same cluster as `airport`.
    Returns [airport] if it belongs to no cluster.
    """
    cluster_name = CLUSTER_OF.get(airport.upper())
    if cluster_name:
        return list(CLUSTERS[cluster_name].airports)
    return [airport.upper()]


def expand_list_to_clusters(airports: List[str]) -> List[str]:
    """
    Expand a list of airports so each is replaced by its full cluster.
    Deduplicates the result while preserving rough order.
    """
    seen: Set[str] = set()
    result: List[str] = []
    for ap in airports:
        for member in expand_to_cluster(ap):
            if member not in seen:
                seen.add(member)
                result.append(member)
    return result


def get_cluster_name(airport: str) -> Optional[str]:
    """Return the cluster city name for an airport, or None."""
    return CLUSTER_OF.get(airport.upper())


def cluster_display_name(airport: str) -> str:
    """Return city cluster name if available, else the airport code itself."""
    return CLUSTER_OF.get(airport.upper(), airport.upper())


def cluster_city_code(airport: str) -> Optional[str]:
    """IATA metropolitan-area code for a clustered airport (MXP → MIL), else None."""
    cluster_name = CLUSTER_OF.get(airport.upper())
    return CLUSTERS[cluster_name].city_code if cluster_name else None


def are_in_same_cluster(a: str, b: str) -> bool:
    cluster_a = CLUSTER_OF.get(a.upper())
    cluster_b = CLUSTER_OF.get(b.upper())
    return cluster_a is not None and cluster_a == cluster_b
