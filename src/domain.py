TRANSICOES = {
    "Na fila": ["Em diagnóstico", "Falhou"],
    "Em diagnóstico": ["Em execução", "Aguardando peças", "Falhou"],
    "Aguardando peças": ["Em execução", "Falhou"],
    "Em execução": ["Finalizada", "Falhou"],
    "Finalizada": [],
    "Falhou": [],
}

STATUS_FINAIS = ("Finalizada", "Falhou")


class DomainError(Exception):
    pass


class Execucao:
    def __init__(self, id_os, itens, status="Na fila"):
        try:
            self.id_os = int(id_os)
            self.itens = [{"descricao": str(item["descricao"]), "tipo": str(item["tipo"])} for item in itens]
        except (KeyError, TypeError, ValueError) as e:
            raise DomainError("Dados da execução inválidos") from e
        self.status = status

    @staticmethod
    def pode_transicionar(status_atual, novo_status):
        return novo_status in TRANSICOES.get(status_atual, [])
