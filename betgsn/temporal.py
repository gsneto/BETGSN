"""Política temporal compartilhada; timestamps ausentes nunca são observações."""
from datetime import timedelta
from .timeutil import parse_kickoff, utc_key


def result_time(match) -> str:
    kickoff = parse_kickoff(match.kickoff, match.timezone)
    if match.result_available_at:
        available = parse_kickoff(match.result_available_at)
        if available <= kickoff:
            raise ValueError("resultado disponível antes de terminar a partida")
        return utc_key(match.result_available_at)
    # 48h após início do dia: exclui também toda a data seguinte sem evidência
    # de publicação. Conservador para arquivos antigos sem hora de término.
    return (kickoff.replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
