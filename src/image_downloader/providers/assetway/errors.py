"""Safe domain errors for the Assetway downloader."""


class AssetwayDownloadError(RuntimeError):
    def __init__(self, code: str, safe_message: str, *, retryable: bool) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


def authentication_required() -> AssetwayDownloadError:
    return AssetwayDownloadError(
        "authentication_required",
        "A autenticação Assetway é necessária. Entre manualmente e tente novamente.",
        retryable=True,
    )


def quality_unverified() -> AssetwayDownloadError:
    return AssetwayDownloadError(
        "quality_unverified",
        "Não foi possível comprovar a opção de maior qualidade do ativo.",
        retryable=False,
    )