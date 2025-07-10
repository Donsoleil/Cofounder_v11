"""
Co-founder GPT v11 - Risk Evaluator Component
Business risk assessment and mitigation strategy system.
"""

import json
from typing import Dict, Any, List

class RiskEvaluator:
    """Risk evaluation system for comprehensive business risk assessment."""
    
    def __init__(self):
        self.risk_categories = {
            "market_risks": ["market_timing", "competition", "customer_adoption", "market_size"],
            "financial_risks": ["funding", "cash_flow", "revenue_model", "cost_structure"],
            "operational_risks": ["team", "technology", "supply_chain", "scaling"],
            "regulatory_risks": ["compliance", "ip_protection", "liability", "privacy"],
            "strategic_risks": ["product_market_fit", "competitive_advantage", "business_model", "pivot_risk"]
        }
    
    def evaluate_business_risks(self, business_context: Dict[str, Any]) -> Dict[str, Any]:
        """Comprehensive risk evaluation for business context."""
        business_challenge = business_context.get('business_challenge', '')
        founder_stage = business_context.get('founder_stage', 'ideate')
        
        # Identify and assess risks
        risk_assessment = self._assess_all_risks(business_challenge, founder_stage)
        
        # Generate mitigation strategies
        mitigation_strategies = self._generate_mitigation_strategies(risk_assessment)
        
        # Create monitoring framework
        monitoring_framework = self._create_monitoring_framework(risk_assessment)
        
        return {
            "risk_evaluation": {
                "risk_assessment": risk_assessment,
                "mitigation_strategies": mitigation_strategies,
                "monitoring_framework": monitoring_framework,
                "critical_risks": self._identify_critical_risks(risk_assessment),
                "risk_summary": self._generate_risk_summary(risk_assessment)
            },
            "risk_insights": self._generate_risk_insights(risk_assessment, founder_stage),
            "confidence_level": 0.8
        }
    
    def _assess_all_risks(self, business_challenge: str, founder_stage: str) -> Dict[str, Any]:
        """Assess risks across all categories."""
        risk_assessment = {}
        
        for category, risks in self.risk_categories.items():
            assessed_risks = []
            for risk in risks:
                risk_data = {
                    "risk_name": risk,
                    "probability": self._assess_probability(risk, founder_stage),
                    "impact": self._assess_impact(risk, business_challenge),
                    "risk_score": 0.0
                }
                risk_data["risk_score"] = risk_data["probability"] * risk_data["impact"]
                risk_data["risk_level"] = self._categorize_risk_level(risk_data["risk_score"])
                assessed_risks.append(risk_data)
            
            risk_assessment[category] = {
                "risks": assessed_risks,
                "category_risk_score": sum(r["risk_score"] for r in assessed_risks) / len(assessed_risks)
            }
        
        return risk_assessment
    
    def _assess_probability(self, risk: str, founder_stage: str) -> float:
        """Assess probability of risk occurring."""
        base_prob = 0.3
        if founder_stage == 'ideate':
            base_prob += 0.2
        elif founder_stage == 'validate':
            base_prob += 0.1
        elif founder_stage == 'scale':
            base_prob -= 0.1
        return min(max(base_prob, 0.0), 1.0)
    
    def _assess_impact(self, risk: str, business_challenge: str) -> float:
        """Assess impact of risk if it occurs."""
        critical_risks = ['funding', 'product_market_fit', 'team', 'competitive_advantage']
        if risk in critical_risks:
            return 0.8
        return 0.5
    
    def _categorize_risk_level(self, risk_score: float) -> str:
        """Categorize risk level."""
        if risk_score >= 0.6:
            return "Critical"
        elif risk_score >= 0.4:
            return "High"
        elif risk_score >= 0.2:
            return "Medium"
        return "Low"
    
    def _generate_mitigation_strategies(self, risk_assessment: Dict[str, Any]) -> Dict[str, Any]:
        """Generate mitigation strategies for high-priority risks."""
        strategies = {}
        
        for category, data in risk_assessment.items():
            category_strategies = []
            for risk in data["risks"]:
                if risk["risk_level"] in ["Critical", "High"]:
                    strategy = {
                        "risk": risk["risk_name"],
                        "strategy": self._get_mitigation_strategy(risk["risk_name"]),
                        "timeline": "1-3 months",
                        "priority": risk["risk_level"]
                    }
                    category_strategies.append(strategy)
            
            strategies[category] = category_strategies
        
        return strategies
    
    def _get_mitigation_strategy(self, risk_name: str) -> str:
        """Get specific mitigation strategy for risk."""
        strategies = {
            "market_timing": "Conduct market validation and timing analysis",
            "competition": "Develop competitive differentiation strategy",
            "funding": "Diversify funding sources and extend runway",
            "team": "Implement retention strategies and succession planning",
            "product_market_fit": "Increase customer validation and iteration speed",
            "technology": "Invest in technical architecture and security",
            "cash_flow": "Optimize pricing and payment terms",
            "compliance": "Implement compliance framework and monitoring"
        }
        return strategies.get(risk_name, "Develop comprehensive risk management plan")
    
    def _create_monitoring_framework(self, risk_assessment: Dict[str, Any]) -> Dict[str, Any]:
        """Create framework for ongoing risk monitoring."""
        return {
            "monitoring_frequency": "Monthly",
            "key_indicators": self._identify_key_indicators(risk_assessment),
            "escalation_triggers": self._define_escalation_triggers(risk_assessment),
            "reporting_structure": "Executive dashboard with risk trends"
        }
    
    def _identify_key_indicators(self, risk_assessment: Dict[str, Any]) -> List[str]:
        """Identify key indicators to monitor."""
        return [
            "Customer acquisition rate",
            "Monthly recurring revenue",
            "Team retention rate",
            "Product usage metrics",
            "Competitive positioning",
            "Funding runway"
        ]
    
    def _define_escalation_triggers(self, risk_assessment: Dict[str, Any]) -> List[str]:
        """Define triggers for risk escalation."""
        return [
            "Critical risk indicators show 20% deterioration",
            "Multiple high risks activated simultaneously",
            "Funding runway drops below 6 months",
            "Key team members give notice",
            "Product metrics show negative trends"
        ]
    
    def _identify_critical_risks(self, risk_assessment: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Identify critical risks requiring immediate attention."""
        critical_risks = []
        
        for category, data in risk_assessment.items():
            for risk in data["risks"]:
                if risk["risk_level"] == "Critical":
                    critical_risks.append({
                        "category": category,
                        "risk": risk["risk_name"],
                        "score": risk["risk_score"],
                        "action_required": "Immediate mitigation plan needed"
                    })
        
        return critical_risks
    
    def _generate_risk_summary(self, risk_assessment: Dict[str, Any]) -> Dict[str, Any]:
        """Generate executive summary of risk assessment."""
        total_risks = sum(len(data["risks"]) for data in risk_assessment.values())
        critical_risks = sum(1 for data in risk_assessment.values() 
                           for risk in data["risks"] if risk["risk_level"] == "Critical")
        
        return {
            "total_risks": total_risks,
            "critical_risks": critical_risks,
            "overall_risk_level": self._calculate_overall_risk_level(risk_assessment),
            "top_risk_category": self._identify_top_risk_category(risk_assessment)
        }
    
    def _calculate_overall_risk_level(self, risk_assessment: Dict[str, Any]) -> str:
        """Calculate overall risk level."""
        avg_score = sum(data["category_risk_score"] for data in risk_assessment.values()) / len(risk_assessment)
        if avg_score >= 0.6:
            return "High"
        elif avg_score >= 0.4:
            return "Medium"
        return "Low"
    
    def _identify_top_risk_category(self, risk_assessment: Dict[str, Any]) -> str:
        """Identify top risk category."""
        return max(risk_assessment.items(), key=lambda x: x[1]["category_risk_score"])[0]
    
    def _generate_risk_insights(self, risk_assessment: Dict[str, Any], founder_stage: str) -> List[str]:
        """Generate strategic insights from risk assessment."""
        insights = []
        
        overall_risk = self._calculate_overall_risk_level(risk_assessment)
        insights.append(f"Overall risk level is {overall_risk}")
        
        if founder_stage == 'ideate':
            insights.append("Focus on market validation to reduce strategic risks")
        elif founder_stage == 'validate':
            insights.append("Prioritize product-market fit and early customer success")
        elif founder_stage == 'build':
            insights.append("Focus on operational excellence and team building")
        elif founder_stage == 'scale':
            insights.append("Manage financial and competitive risks for sustainable growth")
        
        return insights 