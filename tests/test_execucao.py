from decimal import Decimal

import pytest

import application
from domain import DomainError, Execucao
from infrastructure import para_json


def test_comando_coloca_a_os_na_fila(ambiente):
    ambiente.iniciar(1)
    fila = ambiente.cliente.get('/execucao/fila', headers=ambiente.token()).get_json()
    assert [e['id_os'] for e in fila] == [1]
    assert fila[0]['status'] == 'Na fila'
    assert fila[0]['itens'] == [{'descricao': 'Troca de óleo', 'tipo': 'servico'}]
    assert ambiente.eventos() == []


def test_comando_repetido_nao_duplica(ambiente):
    ambiente.iniciar(2)
    ambiente.iniciar(2)
    assert len(ambiente.cliente.get('/execucao/fila', headers=ambiente.token()).get_json()) == 1


def test_fluxo_completo_publica_cada_status(ambiente):
    ambiente.iniciar(3)
    for status in ['Em diagnóstico', 'Aguardando peças', 'Em execução', 'Finalizada']:
        resposta = ambiente.status(3, status)
        assert resposta.status_code == 200
        assert resposta.get_json()['status'] == status

    eventos = ambiente.eventos()
    assert [e['tipo'] for e in eventos] == ['ExecucaoAtualizada'] * 4
    assert [e['dados']['status'] for e in eventos] == ['Em diagnóstico', 'Aguardando peças', 'Em execução', 'Finalizada']

    execucao = ambiente.cliente.get('/execucao/ordens/3', headers=ambiente.token()).get_json()
    assert [h['status'] for h in execucao['historico']] == [
        'Na fila', 'Em diagnóstico', 'Aguardando peças', 'Em execução', 'Finalizada'
    ]
    assert ambiente.cliente.get('/execucao/fila', headers=ambiente.token()).get_json() == []


def test_status_repetido_reemite_o_evento(ambiente):
    ambiente.iniciar(4)
    ambiente.status(4, 'Em diagnóstico')
    ambiente.eventos()
    assert ambiente.status(4, 'Em diagnóstico').status_code == 200
    assert [e['dados']['status'] for e in ambiente.eventos()] == ['Em diagnóstico']


def test_transicao_fora_de_ordem_e_status_invalido(ambiente):
    ambiente.iniciar(5)
    assert ambiente.status(5, 'Finalizada').status_code == 400
    assert ambiente.status(5, 'Na fila').status_code == 400
    assert ambiente.status(999, 'Em diagnóstico').status_code == 404


def test_falha_na_execucao_publica_evento_de_compensacao(ambiente):
    ambiente.iniciar(6)
    ambiente.status(6, 'Em diagnóstico')
    ambiente.eventos()

    resposta = ambiente.cliente.post(
        '/execucao/ordens/6/falha', json={'motivo': 'Peça indisponível'}, headers=ambiente.token()
    )
    assert resposta.status_code == 200
    eventos = ambiente.eventos()
    assert eventos[0]['tipo'] == 'ExecucaoFalhou'
    assert eventos[0]['dados']['motivo'] == 'Peça indisponível'
    assert application.execucao_repo.buscar(6)['motivo_falha'] == 'Peça indisponível'

    sem_motivo = ambiente.cliente.post('/execucao/ordens/6/falha', json={}, headers=ambiente.token())
    assert sem_motivo.status_code == 400
    assert ambiente.status(6, 'Em execução').status_code == 400


def test_rotas_exigem_funcionario(ambiente):
    assert ambiente.cliente.get('/execucao/fila').status_code == 401
    assert ambiente.cliente.get('/execucao/fila', headers=ambiente.token('cliente', 7)).status_code == 403


def test_mensagens_invalidas_sao_descartadas(ambiente):
    ambiente.enviar_comando('ComandoQueNaoExiste', 7)
    ambiente.enviar_comando('IniciarExecucao', 8, {'itens': [{'tipo': 'servico'}]})
    application.processar_comandos()
    application.processar_comandos()
    assert application.execucao_repo.buscar(8) is None


def test_falha_inesperada_mantem_a_mensagem(ambiente, monkeypatch):
    def quebrar(*args):
        raise RuntimeError('DynamoDB fora do ar')

    monkeypatch.setattr(application.execucao_repo, 'criar', quebrar)
    ambiente.enviar_comando('IniciarExecucao', 9, {'itens': []})
    application.processar_comandos()
    monkeypatch.undo()
    url = ambiente.sqs.get_queue_url(QueueName='execucao-comandos-teste.fifo')['QueueUrl']
    atributos = ambiente.sqs.get_queue_attributes(QueueUrl=url, AttributeNames=['All'])['Attributes']
    assert int(atributos['ApproximateNumberOfMessagesNotVisible']) == 1


def test_conflito_de_concorrencia(ambiente, monkeypatch):
    ambiente.iniciar(10)
    monkeypatch.setattr(application.execucao_repo, 'atualizar_status', lambda *args: False)
    assert ambiente.status(10, 'Em diagnóstico').status_code == 400


def test_health_ready_e_swagger(ambiente, monkeypatch):
    assert ambiente.cliente.get('/execucao/health').status_code == 200
    assert ambiente.cliente.get('/execucao/ready').status_code == 200
    assert ambiente.cliente.get('/execucao/apidocs/').status_code == 200
    assert '/execucao/fila' in ambiente.cliente.get('/execucao/apispec.json').get_json()['paths']

    def quebrar():
        raise RuntimeError('fora do ar')

    monkeypatch.setattr(application.execucao_repo, 'ping', quebrar)
    assert ambiente.cliente.get('/execucao/ready').status_code == 503


def test_dominio():
    with pytest.raises(DomainError):
        Execucao('abc', [])
    assert Execucao(1, [{'descricao': 'Revisão', 'tipo': 'servico'}]).status == 'Na fila'
    assert Execucao.pode_transicionar('Em execução', 'Falhou')
    assert not Execucao.pode_transicionar('Finalizada', 'Falhou')


def test_para_json():
    assert para_json(Decimal(3)) == 3
    assert para_json(Decimal('2.5')) == 2.5
    with pytest.raises(TypeError):
        para_json(object())
