import json
import os
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError
from datadog.dogstatsd import DogStatsd

from domain import STATUS_FINAIS
from ports import ExecucaoRepositoryPort, MensageriaPort, MetricasPort


def para_json(valor):
    if isinstance(valor, Decimal):
        return int(valor) if valor == valor.to_integral_value() else float(valor)
    raise TypeError(f'Tipo não serializável: {type(valor).__name__}')


def agora():
    return datetime.now(timezone.utc).isoformat()


class ExecucaoRepositoryDynamo(ExecucaoRepositoryPort):
    def __init__(self, nome_tabela):
        self.tabela = boto3.resource('dynamodb').Table(nome_tabela)

    def buscar(self, id_os):
        return self.tabela.get_item(Key={'id_os': int(id_os)}).get('Item')

    def listar_ativas(self):
        filtro = Attr('status').ne(STATUS_FINAIS[0]) & Attr('status').ne(STATUS_FINAIS[1])
        resposta = self.tabela.scan(FilterExpression=filtro)
        itens = resposta['Items']
        while 'LastEvaluatedKey' in resposta:
            resposta = self.tabela.scan(FilterExpression=filtro, ExclusiveStartKey=resposta['LastEvaluatedKey'])
            itens.extend(resposta['Items'])
        return itens

    def criar(self, execucao):
        momento = agora()
        try:
            self.tabela.put_item(
                Item={
                    'id_os': execucao.id_os, 'itens': execucao.itens, 'status': execucao.status,
                    'historico': [{'status': execucao.status, 'data': momento}],
                    'criado_em': momento, 'atualizado_em': momento,
                },
                ConditionExpression='attribute_not_exists(id_os)'
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                return False
            raise

    def atualizar_status(self, id_os, status_atual, novo_status, motivo=None):
        momento = agora()
        expressao = 'SET #status = :novo, atualizado_em = :momento, historico = list_append(historico, :evento)'
        valores = {
            ':novo': novo_status, ':momento': momento, ':atual': status_atual,
            ':evento': [{'status': novo_status, 'data': momento}],
        }
        if motivo:
            expressao += ', motivo_falha = :motivo'
            valores[':motivo'] = motivo
        try:
            self.tabela.update_item(
                Key={'id_os': int(id_os)},
                UpdateExpression=expressao,
                ConditionExpression='#status = :atual',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues=valores,
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                return False
            raise

    def ping(self):
        resposta = self.tabela.meta.client.describe_table(TableName=self.tabela.name)
        return resposta['Table']['TableStatus'] == 'ACTIVE'


class MensageriaSQS(MensageriaPort):
    def __init__(self):
        self.sqs = boto3.client('sqs')
        self.urls = {}
        self.espera = int(os.getenv('SQS_ESPERA_SEGUNDOS', '10'))

    def _url(self, fila):
        if fila not in self.urls:
            self.urls[fila] = self.sqs.get_queue_url(QueueName=fila)['QueueUrl']
        return self.urls[fila]

    def enviar(self, fila, mensagem):
        self.sqs.send_message(
            QueueUrl=self._url(fila),
            MessageBody=json.dumps(mensagem, default=para_json),
            MessageGroupId=str(mensagem['id_os']),
        )

    def receber(self, fila):
        resposta = self.sqs.receive_message(
            QueueUrl=self._url(fila), MaxNumberOfMessages=10, WaitTimeSeconds=self.espera
        )
        return [(self._ler(m['Body']), m['ReceiptHandle']) for m in resposta.get('Messages', [])]

    def confirmar(self, fila, recibo):
        self.sqs.delete_message(QueueUrl=self._url(fila), ReceiptHandle=recibo)

    @staticmethod
    def _ler(corpo):
        try:
            mensagem = json.loads(corpo)
        except json.JSONDecodeError:
            return {}
        return mensagem if isinstance(mensagem, dict) else {}


def _tag(valor):
    return unicodedata.normalize('NFKD', valor).encode('ascii', 'ignore').decode().lower().replace(' ', '_')


class MetricasDatadog(MetricasPort):
    def __init__(self):
        self.statsd = DogStatsd(
            host=os.getenv('DD_AGENT_HOST', 'localhost'),
            port=int(os.getenv('DD_DOGSTATSD_PORT', '8125')),
            disable_buffering=True,
        )

    def status_execucao(self, status):
        self.statsd.increment('oficina.execucao.status', tags=[f'status:{_tag(status)}'])

    def erro_integracao(self, integracao, motivo):
        self.statsd.increment('oficina.integracao.erros', tags=[f'integracao:{integracao}', f'motivo:{motivo}'])
