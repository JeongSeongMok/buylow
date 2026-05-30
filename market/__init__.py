"""시장 정의 — 백테스트(오케스트레이터/ETL)와 전략(LEAN 런타임) 양쪽이 공유하는 경량 모듈.

AlgorithmImports(.NET) 의존 없이 stdlib만 사용한다(단위 테스트 가능). LEAN 안에서 쓰는
FeeModel/베이스 전략은 strategies/krx.py에 있고, 여기 순수 로직을 가져다 쓴다.
"""
