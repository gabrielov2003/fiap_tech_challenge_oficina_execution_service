import logging

from domain import DomainError, Execucao
from ports import ExecucaoRepositoryPort, MensageriaPort, MetricasPort

logger = logging.getLogger('execucao.aplicacao')

execucao_repo: ExecucaoRepositoryPort = None
mensageria: MensageriaPort = None
metricas: MetricasPort = None
filas = {'comandos': None, 'eventos_os': None}


class MensagemInvalida(Exception):
    pass


def publicar_evento(execucao, correlation_id=None):
    tipo = 'ExecucaoFalhou' if execucao['status'] == "Falhou" else 'ExecucaoAtualizada'
    mensageria.enviar(filas['eventos_os'], {
        'tipo': tipo,
        'id_os': execucao['id_os'],
        'dados': {'status': execucao['status'], 'motivo': execucao.get('motivo_falha')},
        'correlation_id': correlation_id,
    })
    logger.info('evento_publicado', extra={'tipo': tipo, 'id_os': execucao['id_os'], 'correlation_id': correlation_id})


class ExecucaoService:
    @staticmethod
    def listar_fila():
        return sorted(execucao_repo.listar_ativas(), key=lambda execucao: execucao['criado_em'])

    @staticmethod
    def buscar(id_os):
        return execucao_repo.buscar(id_os)

    @staticmethod
    def iniciar_execucao(id_os, dados, correlation_id=None):
        try:
            execucao = Execucao(id_os, dados.get('itens') or [])
        except DomainError as e:
            raise MensagemInvalida(str(e)) from e
        if not execucao_repo.criar(execucao):
            logger.info('execucao_ja_enfileirada', extra={'id_os': execucao.id_os, 'correlation_id': correlation_id})
            return
        metricas.status_execucao(execucao.status)
        logger.info('execucao_enfileirada', extra={'id_os': execucao.id_os, 'correlation_id': correlation_id})

    @staticmethod
    def atualizar_status(id_os, novo_status, motivo=None, correlation_id=None):
        execucao = execucao_repo.buscar(id_os)
        if not execucao:
            return None
        if execucao['status'] == novo_status:
            publicar_evento(execucao, correlation_id)
            return execucao
        if not Execucao.pode_transicionar(execucao['status'], novo_status):
            logger.warning('transicao_rejeitada', extra={
                'id_os': id_os, 'status_atual': execucao['status'], 'status_solicitado': novo_status
            })
            return {"erro": f"Transição inválida de '{execucao['status']}' para '{novo_status}'"}
        if not execucao_repo.atualizar_status(id_os, execucao['status'], novo_status, motivo):
            return {"erro": "A execução foi alterada por outra operação, tente novamente"}
        atualizada = {**execucao, 'status': novo_status, 'motivo_falha': motivo or execucao.get('motivo_falha')}
        metricas.status_execucao(novo_status)
        logger.info('status_execucao_alterado', extra={
            'id_os': id_os, 'status_anterior': execucao['status'], 'novo_status': novo_status,
            'correlation_id': correlation_id
        })
        publicar_evento(atualizada, correlation_id)
        return atualizada

    @staticmethod
    def registrar_falha(id_os, motivo, correlation_id=None):
        return ExecucaoService.atualizar_status(id_os, "Falhou", motivo, correlation_id)

    @staticmethod
    def banco_disponivel():
        try:
            return execucao_repo.ping()
        except Exception:
            logger.exception('banco_indisponivel')
            return False


COMANDOS = {
    'IniciarExecucao': ExecucaoService.iniciar_execucao,
}


def tratar_comando(mensagem):
    acao = COMANDOS.get(mensagem.get('tipo'))
    if not acao or not mensagem.get('id_os'):
        raise MensagemInvalida(f"Comando inválido: {mensagem.get('tipo')}")
    acao(mensagem['id_os'], mensagem.get('dados') or {}, mensagem.get('correlation_id'))


def processar_comandos():
    for mensagem, recibo in mensageria.receber(filas['comandos']):
        contexto = {
            'tipo': mensagem.get('tipo'), 'id_os': mensagem.get('id_os'), 'correlation_id': mensagem.get('correlation_id')
        }
        try:
            tratar_comando(mensagem)
        except MensagemInvalida:
            logger.warning('mensagem_invalida', extra=contexto)
            metricas.erro_integracao('sqs', 'mensagem_invalida')
        except Exception:
            logger.exception('falha_processamento_mensagem', extra=contexto)
            metricas.erro_integracao('sqs', 'falha_processamento')
            continue
        mensageria.confirmar(filas['comandos'], recibo)
