export class QuestionInputError extends Error {
  readonly code = 'QUESTION_INPUT_INVALID'

  constructor(message: string) {
    super(message)
    this.name = 'QuestionInputError'
  }
}

export class QuestionBindingError extends Error {
  readonly code = 'QUESTION_BINDING_CONFLICT'

  constructor(message: string) {
    super(message)
    this.name = 'QuestionBindingError'
  }
}

export class QuestionAnswerValidationError extends Error {
  readonly code = 'QUESTION_ANSWER_VALIDATION_FAILED'

  constructor(message: string) {
    super(message)
    this.name = 'QuestionAnswerValidationError'
  }
}

export class QuestionAbortedError extends Error {
  readonly code = 'QUESTION_ABORTED'

  constructor() {
    super('追问已取消')
    this.name = 'QuestionAbortedError'
  }
}
