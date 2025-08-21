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
package com.ibm.websphere.samples.daytrader.web.prims.ejb3;

import java.io.IOException;

import javax.annotation.PostConstruct;
import javax.inject.Inject;
import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.daytrader.impl.ejb3.TradeSLSBBean;
import com.ibm.websphere.samples.daytrader.interfaces.TradeEJB;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;

/**
 *
 * This primitive is designed to run inside the TradeApplication and relies upon
 * the {@link trade_client.TradeConfig} class to set configuration parameters.
 * PingServlet2SessionEJB tests key functionality of a servlet call to a
 * stateless SessionEJB. This servlet makes use of the Stateless Session EJB
 * {@link trade.Trade} by calling calculateInvestmentReturn with three random
 * numbers.
 *
 */
@WebServlet(name = "ejb3.PingServlet2Session", urlPatterns = { "/ejb3/PingServlet2Session" })
public class PingServlet2Session extends HttpServlet {

    private static final long serialVersionUID = 6854998080392777053L;

    private static String initTime;

    private static int hitCount;

    @Inject
    @TradeEJB
    private TradeServices tradeSLSBLocal;

    
    @Override
    public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        doGet(req, res);
    }

    @Override
    public void doGet(HttpServletRequest req, HttpServletResponse res) throws IOException, ServletException {

        res.setContentType("text/html");
        java.io.PrintWriter out = res.getWriter();
        // use a stringbuffer to avoid concatenation of Strings
        StringBuffer output = new StringBuffer(100);
        output.append("<html><head><title>PingServlet2SessionLocal</title></head>"
                + "<body><HR><FONT size=\"+2\" color=\"#000066\">PingServlet2SessionLocal<BR></FONT>" + "<FONT size=\"-1\" color=\"#000066\">"
                + "Tests the basis path from a Servlet to a Session Bean.");

        try {

            try {
                // create three random numbers
                double rnd1 = Math.random() * 1000000;
                double rnd2 = Math.random() * 1000000;

                // use a function to do some work.
                double increase = 0.0;
                int iter = TradeConfig.getPrimIterations();
                for (int ii = 0; ii < iter; ii++) {
                    increase = tradeSLSBLocal.investmentReturn(rnd1, rnd2);
                }

                // write out the output
                output.append("<HR>initTime: " + initTime);
                output.append("<BR>Hit Count: " + hitCount++);
                output.append("<HR>Investment Return Information <BR><BR>investment: " + rnd1);
                output.append("<BR>current Value: " + rnd2);
                output.append("<BR>investment return " + increase + "<HR></FONT></BODY></HTML>");
                out.println(output.toString());

            } catch (Exception e) {
                Log.error("PingServlet2Session.doGet(...):exception calling trade.investmentReturn ");
                throw e;
            }
        } // this is where I actually handle the exceptions
        catch (Exception e) {
            Log.error(e, "PingServlet2Session.doGet(...): error");
            res.sendError(500, "PingServlet2Session.doGet(...): error, " + e.toString());

        }
    }

    @Override
    public String getServletInfo() {
        return "web primitive, configured with trade runtime configs, tests Servlet to Session EJB path";

    }

    @Override
    public void init(ServletConfig config) throws ServletException {
        super.init(config);
        hitCount = 0;
        initTime = new java.util.Date().toString();
    }
}