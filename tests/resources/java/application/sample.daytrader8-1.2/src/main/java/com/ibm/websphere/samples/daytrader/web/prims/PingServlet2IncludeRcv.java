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
package com.ibm.websphere.samples.daytrader.web.prims;

import java.io.IOException;

import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

/**
 *
 * PingServlet2Include tests servlet to servlet request dispatching. Servlet 1,
 * the controller, creates a new JavaBean object forwards the servlet request
 * with the JavaBean added to Servlet 2. Servlet 2 obtains access to the
 * JavaBean through the Servlet request object and provides the dynamic HTML
 * output based on the JavaBean data. PingServlet2Servlet is the initial servlet
 * that sends a request to {@link PingServlet2ServletRcv}
 *
 */
@WebServlet(name = "PingServlet2IncludeRcv", urlPatterns = { "/servlet/PingServlet2IncludeRcv" })
public class PingServlet2IncludeRcv extends HttpServlet {

    private static final long serialVersionUID = 2628801298561220872L;

    /**
     * forwards post requests to the doGet method Creation date: (11/6/2000
     * 10:52:39 AM)
     *
     * @param res
     *            javax.servlet.http.HttpServletRequest
     * @param res2
     *            javax.servlet.http.HttpServletResponse
     */
    @Override
    public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        doGet(req, res);
    }

    /**
     * this is the main method of the servlet that will service all get
     * requests.
     *
     * @param request
     *            HttpServletRequest
     * @param responce
     *            HttpServletResponce
     **/
    @Override
    public void doGet(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        // do nothing but get included by PingServlet2Include
    }
}