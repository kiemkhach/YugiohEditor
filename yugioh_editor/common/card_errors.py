class CardError(Exception):
    """Base exception for card-editing operations."""


class CardValidationError(CardError):
    def __init__(self, errors: list[str] | tuple[str, ...]) -> None:
        self.errors = tuple(str(error) for error in errors)
        super().__init__("Card validation failed:\n- " + "\n- ".join(self.errors))


class CardCapacityError(CardValidationError):
    def __init__(self, message: str) -> None:
        super().__init__([message])


class CardPersistenceError(CardError):
    pass


class CardImageError(CardError):
    pass


class CardImageNotFoundError(CardImageError):
    """The requested card or its image does not exist at the provider."""


class CardImageParserError(CardImageError):
    """The provider response could not be interpreted as a card image."""


class CardImageTransportError(CardImageError):
    """The provider could not be reached or returned an HTTP failure."""


class CardImageNameConflictError(CardImageError):
    pass


class CardImportError(CardError):
    pass


class CardSuggestionError(CardError):
    pass


class CardReferenceAmbiguityError(CardSuggestionError):
    pass


class CardReferenceDataError(CardError):
    pass


class CardReferenceDataResourceError(CardReferenceDataError):
    pass


class CardReferenceDataConflictError(CardReferenceDataError):
    pass


class JapaneseReadingNotFoundError(CardReferenceDataError):
    pass


class JapaneseReadingCrawlError(CardReferenceDataError):
    pass
