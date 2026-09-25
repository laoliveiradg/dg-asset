# Política de qualidade dos arquivos

## Regra principal

A maior qualidade disponível é obrigatória em qualquer processo futuro de download. A aplicação não deve substituir automaticamente uma imagem de qualidade superior por uma variante reduzida.

## Prioridades

- raster deve priorizar a maior resolução disponível;
- arquivos vetoriais ou originais devem ser priorizados quando aplicáveis;
- preview não é resultado válido;
- thumbnail não é resultado válido;
- screenshot da página não é resultado válido;
- imagem com watermark não é resultado válido;
- baixa resolução não deve ser usada como fallback silencioso.

## Validação futura por provider

Cada provider deve validar a qualidade antes de marcar o item como concluído. Se a maior qualidade esperada não puder ser obtida, o item deve entrar em falha ou pendência. Não deve haver substituição silenciosa por baixa qualidade.

## Regras de rejeição

Os itens abaixo devem ser rejeitados em qualquer fluxo futuro de download:

- preview;
- thumbnail;
- screenshot;
- arquivos com watermark;
- versões reduzidas quando houver uma superior.

## Limite desta etapa

Essa documentação estabelece a regra do produto, mas não implementa validação física de qualidade nem downloads reais.
