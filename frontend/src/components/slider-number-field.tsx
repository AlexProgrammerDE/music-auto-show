import { useEffect, useRef } from "react"

import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Slider } from "@/components/ui/slider"

type SliderNumberFieldProps = {
  readonly id: string
  readonly label: string
  readonly name: string
  readonly value: number
  readonly min: number
  readonly max: number
  readonly step: number
  readonly unit: string
  readonly description?: string
  readonly displayScale?: number
  readonly onBlur?: () => void
  readonly onChange: (value: number) => void
}

export function SliderNumberField({
  id,
  label,
  name,
  value,
  min,
  max,
  step,
  unit,
  description,
  displayScale = 1,
  onBlur,
  onChange,
}: SliderNumberFieldProps) {
  const sliderRef = useRef<HTMLDivElement>(null)
  const displayValue = value * displayScale
  const displayMin = min * displayScale
  const displayMax = max * displayScale
  const displayStep = step * displayScale

  useEffect(() => {
    const sliderInputs =
      sliderRef.current?.querySelectorAll<HTMLInputElement>('input[type="range"]')
    sliderInputs?.forEach((input) => input.setAttribute("aria-label", `${label} slider`))
  }, [label])

  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      {description ? <FieldDescription>{description}</FieldDescription> : null}
      <div className="grid grid-cols-[minmax(7rem,1fr)_6.5rem] items-center gap-3">
        <Slider
          ref={sliderRef}
          min={displayMin}
          max={displayMax}
          step={displayStep}
          value={[displayValue]}
          onValueChange={(next) => {
            const nextValue = Array.isArray(next) ? next[0] : next
            if (nextValue !== undefined) onChange(nextValue / displayScale)
          }}
        />
        <InputGroup>
          <InputGroupInput
            id={id}
            name={name}
            type="number"
            inputMode="decimal"
            autoComplete="off"
            min={displayMin}
            max={displayMax}
            step={displayStep}
            value={displayValue}
            onBlur={onBlur}
            onChange={(event) => onChange(event.target.valueAsNumber / displayScale)}
          />
          <InputGroupAddon align="inline-end">{unit}</InputGroupAddon>
        </InputGroup>
      </div>
    </Field>
  )
}
