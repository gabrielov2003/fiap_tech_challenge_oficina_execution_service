from functools import wraps

from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt
from flasgger import swag_from

from application import ExecucaoService

execucao = Blueprint('execucao', __name__)

ADMIN = 'admin'
STATUS_MANUAIS = ["Em diagnóstico", "Aguardando peças", "Em execução", "Finalizada"]


def papel_requerido(*papeis):
    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            if get_jwt().get('role') not in papeis:
                return jsonify({"erro": "Acesso negado para este perfil"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def responder(resultado):
    if resultado is None:
        return jsonify({"erro": "Execução não encontrada"}), 404
    if 'erro' in resultado:
        return jsonify(resultado), 400
    return jsonify(resultado), 200


@execucao.route('/fila', methods=['GET'])
@papel_requerido(ADMIN)
@swag_from({
    'tags': ['Execução'],
    'description': 'Fila de execução: ordens de serviço pagas e ainda não finalizadas, da mais antiga para a mais nova',
    'responses': {200: {'description': 'Lista de execuções ativas'}}
})
def listar_fila():
    return jsonify(ExecucaoService.listar_fila()), 200


@execucao.route('/ordens/<int:id_os>', methods=['GET'])
@papel_requerido(ADMIN)
@swag_from({
    'tags': ['Execução'],
    'description': 'Dados da execução de uma OS, com itens e histórico de status',
    'parameters': [{'name': 'id_os', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1}],
    'responses': {
        200: {'description': 'Execução encontrada'},
        404: {'description': 'Execução não encontrada'}
    }
})
def get_execucao(id_os):
    return responder(ExecucaoService.buscar(id_os))


@execucao.route('/ordens/<int:id_os>/status', methods=['PUT'])
@papel_requerido(ADMIN)
@swag_from({
    'tags': ['Execução'],
    'description': (
        'Atualiza o andamento da OS: Na fila, Em diagnóstico, Aguardando peças (opcional), Em execução e Finalizada. '
        'Cada mudança é enviada ao OS Service pela fila.'
    ),
    'parameters': [
        {'name': 'id_os', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1},
        {
            'name': 'body', 'in': 'body', 'required': True,
            'schema': {
                'type': 'object',
                'required': ['status'],
                'properties': {
                    'status': {'type': 'string', 'enum': STATUS_MANUAIS, 'example': 'Em diagnóstico'}
                }
            }
        }
    ],
    'responses': {
        200: {'description': 'Status atualizado'},
        400: {'description': 'Status inválido ou transição fora da ordem'},
        404: {'description': 'Execução não encontrada'}
    }
})
def atualizar_status(id_os):
    novo_status = (request.json or {}).get('status')
    if novo_status not in STATUS_MANUAIS:
        return jsonify({"erro": f"Status inválido, use um destes: {', '.join(STATUS_MANUAIS)}"}), 400
    return responder(ExecucaoService.atualizar_status(id_os, novo_status, correlation_id=g.get('correlation_id')))


@execucao.route('/ordens/<int:id_os>/falha', methods=['POST'])
@papel_requerido(ADMIN)
@swag_from({
    'tags': ['Execução'],
    'description': (
        'Registra uma falha na execução, por exemplo peça indisponível. '
        'O OS Service cancela a OS e pede o estorno do pagamento ao Billing (compensação da Saga).'
    ),
    'parameters': [
        {'name': 'id_os', 'in': 'path', 'type': 'integer', 'required': True, 'example': 1},
        {
            'name': 'body', 'in': 'body', 'required': True,
            'schema': {
                'type': 'object',
                'required': ['motivo'],
                'properties': {'motivo': {'type': 'string', 'example': 'Peça indisponível no fornecedor'}}
            }
        }
    ],
    'responses': {
        200: {'description': 'Falha registrada'},
        400: {'description': 'Motivo ausente ou execução já encerrada'},
        404: {'description': 'Execução não encontrada'}
    }
})
def registrar_falha(id_os):
    motivo = (request.json or {}).get('motivo')
    if not motivo:
        return jsonify({"erro": "'motivo' é obrigatório"}), 400
    return responder(ExecucaoService.registrar_falha(id_os, motivo, g.get('correlation_id')))


@execucao.route('/health', methods=['GET'])
@swag_from({
    'tags': ['Saúde'],
    'description': 'Liveness: indica que o serviço está no ar',
    'security': [],
    'responses': {200: {'description': 'Serviço no ar'}}
})
def health():
    return jsonify({"status": "ok"}), 200


@execucao.route('/ready', methods=['GET'])
@swag_from({
    'tags': ['Saúde'],
    'description': 'Readiness: indica que o serviço consegue acessar a tabela no DynamoDB',
    'security': [],
    'responses': {
        200: {'description': 'Pronto para receber tráfego'},
        503: {'description': 'Banco de dados indisponível'}
    }
})
def ready():
    if not ExecucaoService.banco_disponivel():
        return jsonify({"status": "indisponivel"}), 503
    return jsonify({"status": "ok"}), 200
