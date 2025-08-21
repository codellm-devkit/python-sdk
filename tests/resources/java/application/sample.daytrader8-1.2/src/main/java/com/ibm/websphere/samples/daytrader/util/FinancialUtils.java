/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.util;

import java.math.BigDecimal;
import java.util.Collection;
import java.util.Iterator;

import com.ibm.websphere.samples.daytrader.entities.HoldingDataBean;

public class FinancialUtils {

    public static final int ROUND = BigDecimal.ROUND_HALF_UP;
    public static final int SCALE = 2;
    public static final BigDecimal ZERO = (new BigDecimal(0.00)).setScale(SCALE);
    public static final BigDecimal ONE = (new BigDecimal(1.00)).setScale(SCALE);
    public static final BigDecimal HUNDRED = (new BigDecimal(100.00)).setScale(SCALE);

    public static BigDecimal computeGain(BigDecimal currentBalance, BigDecimal openBalance) {
        return currentBalance.subtract(openBalance).setScale(SCALE);
    }

    public static BigDecimal computeGainPercent(BigDecimal currentBalance, BigDecimal openBalance) {
        if (openBalance.doubleValue() == 0.0) {
            return ZERO;
        }
        BigDecimal gainPercent = currentBalance.divide(openBalance, ROUND).subtract(ONE).multiply(HUNDRED);
        return gainPercent;
    }

    public static BigDecimal computeHoldingsTotal(Collection<?> holdingDataBeans) {
        BigDecimal holdingsTotal = new BigDecimal(0.0).setScale(SCALE);
        if (holdingDataBeans == null) {
            return holdingsTotal;
        }
        Iterator<?> it = holdingDataBeans.iterator();
        while (it.hasNext()) {
            HoldingDataBean holdingData = (HoldingDataBean) it.next();
            BigDecimal total = holdingData.getPurchasePrice().multiply(new BigDecimal(holdingData.getQuantity()));
            holdingsTotal = holdingsTotal.add(total);
        }
        return holdingsTotal.setScale(SCALE);
    }

    public static String printGainHTML(BigDecimal gain) {
        String htmlString, arrow;
        if (gain.doubleValue() < 0.0) {
            htmlString = "<FONT color=\"#ff0000\">";
            arrow = "arrowdown.gif";
        } else {
            htmlString = "<FONT color=\"#009900\">";
            arrow = "arrowup.gif";
        }

        htmlString += gain.setScale(SCALE, ROUND) + "</FONT><IMG src=\"images/" + arrow + "\" width=\"10\" height=\"10\" border=\"0\"></IMG>";
        return htmlString;
    }

    public static String printChangeHTML(double change) {
        String htmlString, arrow;
        if (change < 0.0) {
            htmlString = "<FONT color=\"#ff0000\">";
            arrow = "arrowdown.gif";
        } else {
            htmlString = "<FONT color=\"#009900\">";
            arrow = "arrowup.gif";
        }

        htmlString += change + "</FONT><IMG src=\"images/" + arrow + "\" width=\"10\" height=\"10\" border=\"0\"></IMG>";
        return htmlString;
    }

    public static String printGainPercentHTML(BigDecimal gain) {
        String htmlString, arrow;
        if (gain.doubleValue() < 0.0) {
            htmlString = "(<B><FONT color=\"#ff0000\">";
            arrow = "arrowdown.gif";
        } else {
            htmlString = "(<B><FONT color=\"#009900\">+";
            arrow = "arrowup.gif";
        }

        htmlString += gain.setScale(SCALE, ROUND);
        htmlString += "%</FONT></B>)<IMG src=\"images/" + arrow + "\" width=\"10\" height=\"10\" border=\"0\"></IMG>";
        return htmlString;
    }

    public static String printQuoteLink(String symbol) {
        return "<A href=\"app?action=quotes&symbols=" + symbol + "\">" + symbol + "</A>";
    }

}